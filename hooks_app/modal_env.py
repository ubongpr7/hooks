import os
import sys
import modal
import django
import os
from django.core.management import call_command

modal.config.token_id = os.getenv("MODAL_TOKEN_ID")
modal.config.token_secret = os.getenv("MODAL_TOKEN_SECRET")

image = modal.Image.from_registry("nas415/hooks:latest")
app = modal.App(
    name="hook-processor-3",
    image=image
)

@app.function(
    gpu=modal.gpu.A10G(),  
    timeout=3600
)

def process_hook(task_id: int):
    sys.path.insert(0, "/app")
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hooks_app.settings')

    django.setup()

    call_command("process_hook", task_id)



def merge_hook(task_id: int):
    
    sys.path.insert(0, "/app")
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hooks_app.settings')

    django.setup()

    call_command("merge_videos", task_id)


