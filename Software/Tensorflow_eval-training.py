import os
import pandas as pd
import numpy as np
import tensorflow as tf
from tensorflow.keras import layers, models

# -------------------------------------------------------------------------
# 1. SET CONSTANTS AND CONFIGURATIONS
# -------------------------------------------------------------------------
DATASET_DIR = "./dataset"
CSV_PATH = "./labels.csv"
IMG_SIZE = (224, 224)
BATCH_SIZE = 16
EPOCHS = 15

# -------------------------------------------------------------------------
# 2. READ AND PROCESS LABELS
# -------------------------------------------------------------------------
df = pd.read_csv(CSV_PATH)

# Construct absolute file paths
file_paths = [os.path.join(DATASET_DIR, fname) for fname in df['image_id'].values]

# Convert continuous target scores to float32 NumPy arrays
symmetry_labels = df['symmetry'].values.astype(np.float32)
line_labels = df['line_quality'].values.astype(np.float32)
color_labels = df['color_harmony'].values.astype(np.float32)

# -------------------------------------------------------------------------
# 3. HIGH-PERFORMANCE DATA LOADING PIPELINE (tf.data)
# -------------------------------------------------------------------------
def load_and_preprocess_image(path, sym, line, col):
    # Read the raw byte data from disk
    img_raw = tf.io.read_file(path)
    # Decode to RGB tensor image 
    img = tf.image.decode_jpeg(img_raw, channels=3)
    # Resize to the model's expected shape
    img = tf.image.resize(img, IMG_SIZE)
    # Normalise pixel values from [0-255] to [0-1]
    img = img / 255.0
    
    # Return features (X) and a dictionary of target outputs (Y)
    return img, {'symmetry_score': sym, 'line_score': line, 'color_score': col}

# Combine lists into a unified Tensor Dataset
dataset = tf.data.Dataset.from_tensor_slices((file_paths, symmetry_labels, line_labels, color_labels))

# Map the processing function across all files in parallel streams
dataset = dataset.map(load_and_preprocess_image, num_parallel_calls=tf.data.AUTOTUNE)

# Split dataset into 80% Train and 20% Validation sets
DATASET_SIZE = len(df)
train_size = int(0.8 * DATASET_SIZE)

train_dataset = dataset.take(train_size)
val_dataset = dataset.skip(train_size)

# Batch, shuffle, and prefetch for optimal GPU throughput
train_dataset = train_dataset.shuffle(buffer_size=DATASET_SIZE).batch(BATCH_SIZE).prefetch(tf.data.AUTOTUNE)
val_dataset = val_dataset.batch(BATCH_SIZE).prefetch(tf.data.AUTOTUNE)

# -------------------------------------------------------------------------
# 4. DEFINE THE MULTI-HEAD CONVOLUTIONAL MODEL
# -------------------------------------------------------------------------
def create_model():
    # Feature Extractor Base Backbone (MobileNetV3)
    base = tf.keras.applications.MobileNetV3Small(
        input_shape=(IMG_SIZE[0], IMG_SIZE[1], 3),
        include_top=False,
        weights='imagenet',
        pooling='avg'
    )
    base.trainable = False  # Freeze pre-trained weights
    
    shared_features = base.output
    
    # Branch A: Symmetry Output Head
    x1 = layers.Dense(64, activation='relu')(shared_features)
    out_symmetry = layers.Dense(1, name='symmetry_score')(x1)
    
    # Branch B: Line Quality Output Head
    x2 = layers.Dense(64, activation='relu')(shared_features)
    out_line = layers.Dense(1, name='line_score')(x2)
    
    # Branch C: Color Harmony Output Head
    x3 = layers.Dense(64, activation='relu')(shared_features)
    out_color = layers.Dense(1, name='color_score')(x3)
    
    return models.Model(inputs=base.input, outputs=[out_symmetry, out_line, out_color])

model = create_model()

# -------------------------------------------------------------------------
# 5. COMPILE AND EXECUTE TRAINING RUN
# -------------------------------------------------------------------------
model.compile(
    optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
    loss={
        'symmetry_score': 'mse',
        'line_score': 'mse',
        'color_score': 'mse'
    },
    metrics=['mae']
)

print("\n🚀 Starting Model Training...")
history = model.fit(
    train_dataset,
    validation_data=val_dataset,
    epochs=EPOCHS
)

# -------------------------------------------------------------------------
# 6. EXPORT THE WEIGHTS FOR INFERENCE
# -------------------------------------------------------------------------
model.save("rangoli_evaluator_model.keras")
print("\n🎉 Training complete! Model successfully saved as 'rangoli_evaluator_model.keras'")