#!/usr/bin/env python3
"""
Cross-platform GeneralPlus A1800 codec backend.

This backend uses the native Python extension provided by:
    pip install a1800-codec

Import name:
    import a1800_codec
"""

from pathlib import Path
import wave


PCM_FRAME_SAMPLES = 320
PCM_SAMPLE_WIDTH = 2
PCM_FRAME_BYTES = PCM_FRAME_SAMPLES * PCM_SAMPLE_WIDTH


class PortableA1800Backend:
    def __init__(self):
        try:
            import a1800_codec
        except ImportError as exc:
            raise RuntimeError(
                "The portable A1800 codec is not installed.\n"
                "Install it on the build machine with:\n"
                "    python -m pip install a1800-codec"
            ) from exc

        self.codec = a1800_codec

    def decode_file(
        self,
        input_a18_path: str,
        output_wav_path: str,
        sample_rate: int = 16000,
    ) -> int:
        """
        Decode a complete A18 file into mono 16-bit PCM WAV.

        Returns 1 on success to match the existing DLL wrapper behavior.
        """
        input_path = Path(input_a18_path)
        output_path = Path(output_wav_path)

        encoded = input_path.read_bytes()
        if len(encoded) < 6:
            raise ValueError(
                f"A18 input is too small: {input_path} ({len(encoded)} bytes)"
            )

        pcm = bytes(self.codec.decode(encoded))
        if not pcm:
            raise RuntimeError(f"A1800 decoder produced no PCM data: {input_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm)

        return 1

    def encode_file(
        self,
        input_wav_path: str,
        output_a18_path: str,
        bitrate: int = 16000,
        expected_sample_rate: int = 16000,
    ) -> str:
        """
        Encode mono 16-bit PCM WAV into a complete A18 file.
        """
        input_path = Path(input_wav_path)
        output_path = Path(output_a18_path)

        with wave.open(str(input_path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frame_count = wav.getnframes()
            pcm = wav.readframes(frame_count)

        if channels != 1:
            raise ValueError(
                f"WAV must be mono; got {channels} channels: {input_path}"
            )

        if sample_width != 2:
            raise ValueError(
                f"WAV must be 16-bit; got sample width {sample_width}: {input_path}"
            )

        if sample_rate != expected_sample_rate:
            raise ValueError(
                f"WAV must be {expected_sample_rate} Hz; "
                f"got {sample_rate} Hz: {input_path}"
            )

        if not pcm:
            raise ValueError(f"WAV contains no PCM data: {input_path}")

        # The codec consumes complete 320-sample / 640-byte PCM frames.
        # Pad the final partial frame rather than silently losing its tail.
        remainder = len(pcm) % PCM_FRAME_BYTES
        if remainder:
            pcm += bytes(PCM_FRAME_BYTES - remainder)

        encoded = bytes(self.codec.encode(pcm, bitrate=bitrate))

        if len(encoded) < 6:
            raise RuntimeError(
                f"A1800 encoder produced an invalid result: {len(encoded)} bytes"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(encoded)

        return str(output_path)