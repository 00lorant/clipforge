
import os
import json
import uuid
import shutil
import zipfile
import subprocess
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
WEB_DIR = BASE_DIR / "web"

OUTPUT_DIR.mkdir(exist_ok=True)
WEB_DIR.mkdir(exist_ok=True)

app = FastAPI(title="ClipForge")

app.mount("/files", StaticFiles(directory=str(OUTPUT_DIR)), name="files")


@app.get("/health")
def health():
    return {"status": "ClipForge is running"}


@app.get("/")
def home():
    index = WEB_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"message": "Welcome to ClipForge! Your app is being built."}


def get_video_duration(video_path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(video_path)
        ],
        capture_output=True,
        text=True,
        check=True
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


@app.post("/api/process")
async def process_video(
    video: UploadFile = File(...),
    clip_count: int = Form(5),
    clip_length: int = Form(30),
    start_time: float = Form(0),
    end_time: float | None = Form(None)
):
    if not video.filename:
        raise HTTPException(400, "Please select a video.")

    if Path(video.filename).suffix.lower() not in {
        ".mp4", ".mov", ".m4v", ".webm", ".mkv"
    }:
        raise HTTPException(400, "Unsupported video format.")

    if not 1 <= clip_count <= 20:
        raise HTTPException(400, "Choose between 1 and 20 clips.")

    if not 10 <= clip_length <= 60:
        raise HTTPException(400, "Clip length must be 10–60 seconds.")

    job_id = uuid.uuid4().hex
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True)

    input_path = job_dir / "original_video"

    try:
        with input_path.open("wb") as destination:
            shutil.copyfileobj(video.file, destination)

        duration = get_video_duration(input_path)

        if duration < clip_length:
            raise HTTPException(
                400,
                "Your video is shorter than the selected clip length."
            )

        clips = []

        for i in range(clip_count):
            if clip_count == 1:
                start = 0
            else:
                start = (
                    (duration - clip_length)
                    * i / (clip_count - 1)
                )

            clip_name = f"clip_{i + 1:02d}.mp4"
            clip_path = job_dir / clip_name

            command = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", str(input_path),
                "-t", str(clip_length),
                "-vf",
                "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                str(clip_path)
            ]

            subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True
            )

            clips.append({
                "name": clip_name,
                "url": f"/files/{job_id}/{clip_name}"
            })

        zip_path = job_dir / "all_clips.zip"

        with zipfile.ZipFile(
            zip_path, "w", zipfile.ZIP_DEFLATED
        ) as archive:
            for clip in clips:
                archive.write(
                    job_dir / clip["name"],
                    arcname=clip["name"]
                )

        return {
            "message": "Your clips are ready!",
            "clips": clips,
            "zip_url": f"/files/{job_id}/all_clips.zip"
        }

    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except subprocess.CalledProcessError as error:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(
            500,
            "Video processing failed. Please try another video."
        )
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, "Something went wrong.")

    finally:
        await video.close()
