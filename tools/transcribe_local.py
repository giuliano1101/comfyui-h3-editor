"""Offline ASR worker; optional faster-whisper dependency. No model downloads."""

import argparse
import json
import os

os.environ.update(
    HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_IMPLICIT_TOKEN="1"
)


def main():
    import av
    import numpy as np
    from faster_whisper import WhisperModel

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--audio", required=True)
    args = parser.parse_args()
    chunks = []
    count = 0
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with av.open(
        args.audio,
        options={
            "probesize": "1048576",
            "analyzeduration": "1000000",
            "protocol_whitelist": "file,pipe",
            "format_whitelist": "wav,mp3,ogg,matroska,webm,mov,mp4,m4a,aac,flac",
        },
    ) as container:
        if not container.streams.audio:
            raise ValueError("no audio")
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                count += converted.samples
                if count > 30 * 16000:
                    raise ValueError("audio exceeds 30 seconds")
                chunks.append(converted.to_ndarray().reshape(-1).copy())
    if count < 1600:
        raise ValueError("audio too short")
    audio = np.concatenate(chunks).astype(np.float32) / 32768
    model = WhisperModel(
        args.model,
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
        local_files_only=True,
    )
    segments, info = model.transcribe(
        audio, beam_size=1, vad_filter=False, condition_on_previous_text=False
    )
    rows = [
        {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()}
        for s in segments
    ]
    text = "".join(r["text"] for r in rows)
    if len(text) > 2000 or len(rows) > 100:
        raise ValueError("transcript too large")
    print(
        json.dumps(
            {
                "text": text,
                "segments": rows,
                "language": info.language,
                "seconds": round(count / 16000, 3),
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
