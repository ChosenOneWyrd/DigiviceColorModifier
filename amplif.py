import wave
import numpy as np

def amplify_wav(input_file, output_file, gain):
    with wave.open(input_file, 'rb') as wf:
        params = wf.getparams()
        frames = wf.readframes(wf.getnframes())

    audio = np.frombuffer(frames, dtype=np.int16)

    # Apply gain
    amplified = audio * gain

    # Prevent clipping
    amplified = np.clip(amplified, -32768, 32767).astype(np.int16)

    with wave.open(output_file, 'wb') as wf:
        wf.setparams(params)  # preserves 16kHz, mono, 16-bit
        wf.writeframes(amplified.tobytes())

# Example usage
amplify_wav("input.wav", "output.wav", gain=1.5)