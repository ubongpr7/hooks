import os
import modal
modal.config.token_id = os.getenv("MODAL_TOKEN_ID")
modal.config.token_secret = os.getenv("MODAL_TOKEN_SECRET")

image = modal.Image.from_registry("nas415/hooks:latest")
app = modal.App(
    name="hook-processor",
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