import os
import sys
import modal
from django.core.management import call_command
import os
import django
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


@app.function(
    gpu=modal.gpu.A10G(),  
    timeout=3600
)
def merge_hook(task_id: int):
    
    sys.path.insert(0, "/app")
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hooks_app.settings')

    django.setup()

    # from hooks.management.commands.process_hook import Command
    call_command("process_hook", task_id)


# if __name__ == "__main__":
#     # Get task_id from command-line arguments
#     if len(sys.argv) < 2:
#         print("Error: Missing task_id argument")
#         sys.exit(1)

#     try:
#         task_id = int(sys.argv[1])
#     except ValueError:
#         print("Error: task_id must be an integer")
#         sys.exit(1)

#     # Run the function inside Modal
#     with app.run():
#         process_hook.remote(task_id)
    




