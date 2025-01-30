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
        "wget https://www.python.org/ftp/python/3.10.14/Python-3.10.14.tgz",
        "tar -xzvf Python-3.10.14.tgz",
        "cd Python-3.10.14 && ./configure --enable-optimizations --with-system-ffi",
        "cd Python-3.10.14 && make -j 16",
        "cd Python-3.10.14 && make altinstall",
        "sed -i 's/<policy domain=\"path\" rights=\"none\" pattern=\"@\\*\"/<!--<policy domain=\"path\" rights=\"none\" pattern=\"@\\*\"-->/' /etc/ImageMagick-6/policy.xml || true",
        "sed -i 's/<policy domain=\"path\" rights=\"none\" pattern=\"@\\*\"/<!--<policy domain=\"path\" rights=\"none\" pattern=\"@\\*\"-->/' /etc/ImageMagick-7/policy.xml || true"
    )
    .copy_local_dir(".", "/app")  # Copy entire project
    .pip_install("-r requirements.txt")
    .env({"PYTHONPATH": "/app"})
)

app = modal.App(
    name="django-hook-processor",
    image=image,
    # secrets=[
    #     modal.Secret.from_name("my-django-secrets"), 
    #     modal.Secret.from_name("elevenlabs-api-key")
    # ]
)


@app.function(
    gpu=modal.gpu.A10G(),  
    timeout=3600,
    mounts=[modal.Mount.from_local_dir("hooks", remote_path="/app/hooks")]
)
def process_hook(task_id: int):
    import os
    import django
    
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
    django.setup()

    from hooks.management.commands.process_hook import Command
    
    command = Command()
    command.handle(task_id=task_id)

    # modal deploy your_modal_script.py


