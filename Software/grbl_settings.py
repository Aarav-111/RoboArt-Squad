#!/usr/bin/env python3
"""Read (and optionally fix) the GRBL settings App_v16 streams to.

The app's console is display-only, so there is no way from inside it to ask
the controller what it thinks its steps/mm are. This does that over the same
serial port the app uses.

    python3 grbl_settings.py                     # read $$ and report
    python3 grbl_settings.py --factor 2          # also show what to write
    python3 grbl_settings.py --factor 2 --apply  # actually write $100/$101
    python3 grbl_settings.py --axis-ratio 20:24 --apply
                                                  # square the axes up from a
                                                  # measured test circle that
                                                  # came out 20 wide x 24 tall
    python3 grbl_settings.py --cap-rate --apply  # clamp $110/$111 to the true
                                                  # 30kHz pulse ceiling at the
                                                  # CURRENT $100/$101
    python3 grbl_settings.py --arc-tolerance 0.0005 --apply
                                                  # tighten $12 so G2/G3 arcs
                                                  # get tessellated into more
                                                  # (smaller) internal moves

CLOSE THE APP FIRST — the serial port is exclusive, only one program can hold
it at a time.

Why a factor: microstepping is invisible to GRBL. It only knows $100/$101
(steps per mm). Doubling the driver's microstepping halves the distance each
step covers, so steps/mm must be multiplied by the same factor or every design
comes out that much too small AND that much too slow (same pulse rate, shorter
pulses). factor = new microstepping / old microstepping:

    full step -> 1/16   factor 16
    1/8       -> 1/16   factor 2
    1/16      -> 1/32   factor 2

If you don't remember the old setting, measure instead: draw the same design
before and after and use  factor = old_size / new_size.

Why --axis-ratio: an oval where the app commanded a circle means the two axes
cover different real distances for the same commanded unit. App_v16 used to
absorb that in software with a per-axis CAL_X/CAL_Y applied inside to_machine,
and that turned out to be the wrong place for it: it made the canvas->machine
map anisotropic, so a commanded circle stopped being a circle in machine units,
and the G-code arc fitter's whole-circle fast path (one G2 for the entire shape)
could never fire — a Circle went out as 7 chained arcs instead of 1. Both CALs
are pinned at 1.0 now and the correction lives here, where it belongs: $101 is
scaled so a commanded unit of Y covers the same ground as a commanded unit of X,
and the geometry the fitter sees stays square.

Draw a Circle, measure the powder ring, and pass the two figures (any unit —
only the ratio is used). Only the axis that came out LONGER is scaled down;
nothing is ever scaled up. That keeps the design inside the mat, and it keeps
the step-pulse rate falling rather than rising into the AVR's ~30 kHz ceiling.

Why --arc-tolerance: GRBL never draws a G2/G3 as a curve — on receipt it
immediately splits the arc into a handful of straight micro-moves and only
those reach the motors. How many depends on $12 versus the arc's radius, and
that split happens inside the firmware: it is never echoed back over serial,
so the G-code log shows one continuous arc with no corner in it while the
motion actually executed has 2-4 real corners hidden inside that one command.
Each of those is a momentary slowdown for the planner to get around, and on a
powder plotter a slowdown is where extra material piles up — visible as
periodic bulges *while* an arc is drawing, not at any junction you can see in
the log. $12 is interpreted by GRBL in raw coordinate units, and this app's
G-code coordinates are scaled ~51x smaller than real mm (MAX_X = 4.7 spans the
240 mm mat), so the factory default of 0.002 is far coarser here than its
number suggests. Tightening it forces more, smaller hidden segments per arc,
which shrinks each hidden corner's angle and lets the planner carry more speed
through it without dumping to nearly zero — fewer/smaller bulges.
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial not installed:  pip3 install pyserial")

DEFAULT_PORT = "/dev/cu.usbmodem1301"
BAUD = 115200

# GRBL on a 16 MHz AVR cannot sustain step pulses much past this.
MAX_STEP_RATE_HZ = 30000

# The app commands in a scaled space: MAX_X units span the whole mat, so one
# G-code "mm" is MM_PER_UNIT real mm. Kept in step with App_v16's MAX_X and
# MAT_SPAN_MM — that is what makes $11/$12 far coarser than their names suggest.
APP_MAX_X   = 4.7
MAT_SPAN_MM = 240.0
MM_PER_UNIT = MAT_SPAN_MM / APP_MAX_X

LABELS = {
    "$100": "X steps/mm",
    "$101": "Y steps/mm",
    "$102": "Z steps/mm",
    "$110": "X max rate (mm/min)",
    "$111": "Y max rate (mm/min)",
    "$112": "Z max rate (mm/min)",
    "$120": "X accel (mm/s^2)",
    "$121": "Y accel (mm/s^2)",
    "$122": "Z accel (mm/s^2)",
    "$11":  "junction deviation (mm)",
    "$12":  "arc tolerance (mm)",
}


def read_settings(ser):
    """Send $$ and collect the reply until GRBL says ok."""
    ser.reset_input_buffer()
    ser.write(b"$$\n")
    settings, deadline = {}, time.time() + 5.0
    while time.time() < deadline:
        line = ser.readline().decode(errors="replace").strip()
        if not line:
            continue
        if line == "ok":
            break
        if line.startswith("$") and "=" in line:
            key, _, val = line.partition("=")
            # GRBL appends a trailing "(description)" on some builds
            settings[key.strip()] = val.split("(")[0].strip()
    return settings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--factor", type=float,
                    help="multiply $100/$101 by this (new microstep / old)")
    ap.add_argument("--axis-ratio", metavar="W:H",
                    help="measured width:height of a drawn test Circle (any "
                         "unit). Scales the longer axis' steps/mm down so the "
                         "two match — see the module docstring")
    ap.add_argument("--apply", action="store_true",
                    help="actually write the new values to GRBL's EEPROM")
    ap.add_argument("--cap-rate", action="store_true",
                    help="clamp $110/$111 down to the true step-pulse "
                         "ceiling at the (post-factor, if given) steps/mm")
    ap.add_argument("--arc-tolerance", type=float,
                    help="write this value to $12 (GRBL's raw coordinate "
                         "units, not real mm — see the module docstring)")
    ap.add_argument("--set", action="append", metavar="$N=V", default=[],
                    help="write an arbitrary setting, e.g. --set '$101=3360'. "
                         "Repeatable.")
    args = ap.parse_args()

    ratio_wh = None
    if args.axis_ratio:
        try:
            w_s, _, h_s = args.axis_ratio.partition(":")
            ratio_wh = (float(w_s), float(h_s))
        except ValueError:
            ratio_wh = None
        if ratio_wh is None or min(ratio_wh) <= 0:
            sys.exit("--axis-ratio expects 'WIDTH:HEIGHT' with two positive "
                     f"numbers, got {args.axis_ratio!r}")

    wants_change = (args.factor or args.cap_rate or ratio_wh
                    or args.arc_tolerance is not None or args.set)
    if args.apply and not wants_change:
        sys.exit("--apply needs --factor, --axis-ratio, --cap-rate, "
                 "--arc-tolerance and/or --set")
    for item in args.set:
        if not item.startswith("$") or "=" not in item:
            sys.exit(f"--set expects '$N=VALUE', got {item!r}")

    try:
        ser = serial.Serial(args.port, BAUD, timeout=1)
    except serial.SerialException as e:
        sys.exit(f"Could not open {args.port}: {e}\n"
                 "Is App_v16 still running? Close it first.")

    with ser:
        time.sleep(2.0)          # GRBL reboots on DTR; wait for its banner
        ser.reset_input_buffer()

        s = read_settings(ser)
        if not s:
            sys.exit("No reply to $$. Wrong port, or the board is not GRBL.")

        print(f"--- current settings on {args.port} ---")
        for key in ("$11", "$12", "$100", "$101", "$102",
                    "$110", "$111", "$120", "$121"):
            if key in s:
                print(f"  {key:5s} = {s[key]:<12s} {LABELS.get(key, '')}")

        if "$12" in s:
            print(f"\n  $12 = {s['$12']} G-code mm "
                  f"~= {float(s['$12']) * MM_PER_UNIT:.3f} mm of real sagitta "
                  f"on arcs")

        if not wants_change:
            print("\nPass --factor, --axis-ratio, --cap-rate, --arc-tolerance "
                  "and/or --set to see what would change.")
            return

        writes = []  # (command, description) queued for EEPROM

        try:
            x_steps, y_steps = float(s["$100"]), float(s["$101"])
        except KeyError:
            sys.exit("GRBL did not report $100/$101.")

        if args.factor:
            x_new, y_new = x_steps * args.factor, y_steps * args.factor
            print(f"\n--- with factor {args.factor:g} ---")
            print(f"  $100={x_new:g}   (was {x_steps:g})")
            print(f"  $101={y_new:g}   (was {y_steps:g})")
            writes += [(f"$100={x_new:g}", "X steps/mm"),
                      (f"$101={y_new:g}", "Y steps/mm")]
            x_steps, y_steps = x_new, y_new  # rate check below uses the new value

        if ratio_wh:
            width, height = ratio_wh
            print(f"\n--- squaring the axes from a {width:g} x {height:g} "
                  f"test circle ---")
            if abs(width - height) / max(width, height) < 0.005:
                # Half a percent is under the width of a powder line; "fixing"
                # it would only be chasing the measurement itself.
                print("  Already square to within measurement error — "
                      "nothing to do.")
            else:
                # An axis that drew LONG covers too much ground per commanded
                # unit, so it needs fewer steps/mm. Only that axis moves, and
                # only downwards: scaling the short axis up instead would push
                # the design past the edge of the mat and raise the step-pulse
                # rate toward the AVR's ceiling (see --cap-rate).
                if width > height:
                    x_new = x_steps * height / width
                    print(f"  X drew {width / height:.4f}x longer than Y")
                    print(f"  $100={x_new:g}   (was {x_steps:g})")
                    writes.append((f"$100={x_new:g}", "X steps/mm"))
                    x_steps = x_new
                else:
                    y_new = y_steps * width / height
                    print(f"  Y drew {height / width:.4f}x longer than X")
                    print(f"  $101={y_new:g}   (was {y_steps:g})")
                    writes.append((f"$101={y_new:g}", "Y steps/mm"))
                    y_steps = y_new
                print(f"  the design shrinks to {min(width, height) / max(width, height):.3f}x "
                      f"on that axis — re-draw the Circle to confirm it is round")

        if args.cap_rate:
            # After a factor change the pulse rate rises by the same factor,
            # so this is the moment the AVR's ~30kHz ceiling can start biting.
            print(f"\n--- step-pulse ceiling at $100/$101={x_steps:g} ---")
            for axis, rate_key, steps in (("X", "$110", x_steps),
                                          ("Y", "$111", y_steps)):
                if rate_key not in s:
                    continue
                rate = float(s[rate_key])
                hz = rate * steps / 60.0
                safe_rate = MAX_STEP_RATE_HZ * 60.0 / steps
                flag = "  <-- OVER LIMIT" if hz > MAX_STEP_RATE_HZ else "  ok"
                print(f"  {axis} {rate_key}={rate:g} -> {hz:,.0f} Hz "
                      f"(ceiling {MAX_STEP_RATE_HZ:,} Hz = {safe_rate:.0f} mm/min){flag}")
                if hz > MAX_STEP_RATE_HZ:
                    # Round down, never up — this only ever tightens the limit.
                    capped = int(safe_rate)
                    print(f"       -> {rate_key}={capped}")
                    writes.append((f"{rate_key}={capped}",
                                  f"{axis} max rate (mm/min)"))

        if args.arc_tolerance is not None:
            old_tol = s.get("$12")
            print(f"\n--- arc tolerance ---")
            print(f"  $12={args.arc_tolerance:g}   (was {old_tol})")
            print(f"  ~= {args.arc_tolerance * MM_PER_UNIT:.4f} mm of real "
                  f"sagitta on the mat "
                  f"(was ~{float(old_tol) * MM_PER_UNIT:.3f} mm)"
                  if old_tol else "")
            writes.append((f"$12={args.arc_tolerance:g}", "arc tolerance"))

        for item in args.set:
            key, _, val = item.partition("=")
            print(f"\n--- explicit set ---")
            print(f"  {key}={val}   (was {s.get(key, '?')})")
            writes.append((item, LABELS.get(key, "explicit")))

        if not writes:
            print("\nNothing to change.")
            return
        if not args.apply:
            print("\nNothing written. Re-run with --apply to write these.")
            return

        print("\nWriting to EEPROM...")
        for cmd, desc in writes:
            ser.write((cmd + "\n").encode())
            reply = ser.readline().decode(errors="replace").strip()
            print(f"  {cmd:14s} ({desc})  ->  {reply or '(no reply)'}")

        print("\nVerifying:")
        again = read_settings(ser)
        for cmd, _ in writes:
            key = cmd.split("=")[0]
            print(f"  {key} = {again.get(key, '?')}")


if __name__ == "__main__":
    main()
