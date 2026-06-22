Mechanical Construction
Our robot is an Arduino powered XY plotter. It uses two stepper motors to move in two directions: x & y. The frame is a square made of four 2-foot aluminium bars, connected by custom 3D-printed corners. Each motor is attached to a leadscrew, which turns the motor's spinning motion into linear movement. To keep the parts moving smoothly, we added steel guide rods. These rods help the parts slide in a straight line while holding them in the other axis or movements.
The powder nozzle is attached to the y-axis bar and is shaped like a funnel. A stepper motor controls a gate on the nozzle, which opens and closes to drop the powder. The whole Robot is powered by a mini SMPS which can directly be plugged into the socket.

Key sub-systems of our rangoli maker:
1.	Movement & Positioning - what draws the rangoli.
2.	Colour Dispenser - what dispenses the colour for the rangoli.
3.	Power Supply - what powers the whole system.
Movement & Positioning Mechanism
The Movement and positioning is possible by our XY plotter which is run by an Arduino. Two stepper motors move the directions robot left, right, forward, and backward. 
To make sure it moves accurately, each motor is connected to a lead screw that turns spinning into straight-line movement.

To keep the robot steady and prevent it from wobbling while drawing, we added two steel rods. These rods help the moving parts slide smoothly and stay in place. Everything is held inside a strong 2ft by 2ft square frame made of aluminium bars and 3D-printed corners. This solid build lets the robot draw perfect Rangoli patterns on any flat floor.


<insert actual photo of rangoli maker>
 
Figure XX: Block Diagram of Movement Mechanism


 
Figure XX: Pinout Diagram of Movement Mechanism




  

  

Figure XX: 3D drawing of parts and assembly
Colour Dispensing Mechanism:

The colour dispensing mechanism is implemented through a 3D printed hopper with a nozzle at the bottom which uses a stepper motor to operate a small custom 3D printed gate. When the motor spins, the gate opens to release the powder. To stop the flow, the motor spins back to its starting position, closing the gate.


 	
Figure XX: 3D design of hopper gate	Figure XX: 3D Design of hopper
 
Figure XX: Block Diagram of Colour Dispenser
 
Figure XX: Pinout for Colour Dispenser


The Power Supply:
To ensure stable operation of the high-torque stepper motors and the control electronics, we utilize a dedicated Switched-Mode Power Supply (SMPS) unit. This unit provides a constant output of 12V and 10A, delivering a total power capacity of 120W to handle the peak current demands of the XY plotter mechanism. The SMPS is connected directly to a standard AC plug-point or socket, converting mains electricity into the regulated DC power required by the Arduino and CNC shield. This power solution prevents voltage drops that could lead to motor stalling or precision errors during the intricate Rangoli drawing process.

Parts specifications:

Component	Specification	Qty
NEMA 17 Stepper Motor	40 N·cm, 1.8°	3
Arduino UNO	Compatible board	1
CNC Shield V3	GRBL compatible	1
A4988 Stepper Driver	With heatsink	3
12V SMPS Power Supply	5–10A	1
Smooth Rods	8mm steel rods (38 cm)	6
LM8UU Bearings	Linear bearings	12
Frame (2020 extrusion)	Aluminium profile	3
SG90 Servo	Nozzle control	1
Limit Switch	Endstop	3
Aluminium extrusions	Aluminium profile	4
Lead screw	Dia 8MM	2
Pillow block bearing	KP08 8MM	4
Coupling	5mm x 8mm	4
<img width="468" height="519" alt="image" src="https://github.com/user-attachments/assets/d34eafda-b610-4f52-9400-245977c2f402" />

