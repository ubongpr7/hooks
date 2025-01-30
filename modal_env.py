import modal

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install(
        "wget",
        "build-essential",
        "libssl-dev",
        "libffi-dev",
        "libespeak-dev",
        "zlib1g-dev",
        "libmupdf-dev",
        "libfreetype6-dev",
        "ffmpeg",
        "espeak",
        "imagemagick",
        "git",
        "postgresql",
        "postgresql-contrib",
        "libfreetype6",
        "libfontconfig1",
        "fonts-liberation"
    )
    .run_commands(
        # Remove Python 3.10 build commands (already in base image)
        "sed -i 's/<policy domain=\"path\" rights=\"none\" pattern=\"@\\*\"/<!--<policy domain=\"path\" rights=\"none\" pattern=\"@\\*\"-->/' /etc/ImageMagick-6/policy.xml || true",
        "sed -i 's/<policy domain=\"path\" rights=\"none\" pattern=\"@\\*\"/<!--<policy domain=\"path\" rights=\"none\" pattern=\"@\\*\"-->/' /etc/ImageMagick-7/policy.xml || true"
    )
    .add_local_dir(".", remote_path="/app")  # Updated method
    .pip_install("-r requirements.txt")
    .env({"PYTHONPATH": "/app"})
)

app = modal.App(
    name="django-hook-processor",
    image=image
)

@app.function(
    gpu=modal.gpu.A10G(),  
    timeout=3600
    # Removed mounts (now handled by add_local_dir)
)
def process_hook(task_id: int):
    import os
    import django
    
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
    django.setup()

    from hooks.management.commands.process_hook import Command
    
    command = Command()
    command.handle(task_id=task_id)