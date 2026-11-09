
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
from groq import Groq
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
WEB_DIR = BASE_DIR / "web"

OUTPUT_DIR.mkdir(exist_ok=True)
WEB_DIR.mkdir(exist_ok=True)

app = FastAPI(title="ClipForge")

groq_client = Groq(api_key=os.environ["GROQ_API_KEY"]) if os.getenv("GROQ_API_KEY") else None

app.mount("/files", StaticFiles(directory=str(OUTPUT_DIR)), name="files")


def transcribe_audio(audio_path: Path):
    if groq_client is None:
        raise HTTPException(
            status_code=500,
            detail="Speech-to-text is not configured."
        )

    with audio_path.open("rb") as audio_file:
        transcription = groq_client.audio.transcriptions.create(
            file=(audio_path.name, audio_file),
            model="whisper-large-v3-turbo",
            response_format="verbose_json"
        )

    return transcription


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
    end_time: float | None = Form(None),
    template: str = Form("viral")
):
    if not video.filename:
        raise HTTPException(400, "Please select a video.")

    if Path(video.filename).suffix.lower() not in {
        ".mp4", ".mov", ".m4v", ".webm", ".mkv"
    }:
        raise HTTPException(400, "Unsupported video format.")

    if not 1 <= clip_count <= 20:
        raise HTTPException(400, "Choose between 1 and 20 clips.")
    if template not in {"viral", "clean", "podcast", "gaming"}:
        raise HTTPException(400, "Invalid template selected.")
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
        if end_time is None:
            end_time = duration

        if start_time < 0 or end_time > duration or end_time <= start_time:
            raise HTTPException(
                400,
                "Please choose a valid start and end time."
            )

        range_duration = end_time - start_time
        if range_duration < clip_length:
            raise HTTPException(
                400,
                "The selected time range is shorter than your clip length."
            )
        audio_path = job_dir / "audio.mp3"

        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(input_path),
                "-vn",
                "-ac", "1",
                "-ar", "16000",
                "-b:a", "64k",
                str(audio_path)
            ],
            capture_output=True,
            text=True,
            check=True
        )

        transcription = transcribe_audio(audio_path)
        
        
        
            

        clips = []

        for i in range(clip_count):
            if clip_count == 1:
                start = start_time
            else:
                start = (
                    start_time
                    + (end_time - start_time - clip_length)
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
            "transcription": transcription.text,
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
