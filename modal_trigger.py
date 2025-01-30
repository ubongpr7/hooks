import modal

def trigger_processing(task_id):
    function = modal.Function.lookup("django-hook-processor", "process_hook")
    function.call(task_id)

# modal run your_script.py::process_hook --task-id 123


# @app.function(gpu=modal.gpu.A10G())
# def process_hook_gpu(task_id: int):
#     # GPU version
#     ...

# @app.function(gpu=None)
# def process_hook_cpu(task_id: int):
#     # CPU version
#     ...
