import pytest
import torch  # Add torch import
from fastapi.testclient import TestClient
# from fastapi.responses import FileResponse  # Flake8 reports line 3 unused
import io
import os  # Import os for stat mocking
import time  # Import time for stat mocking
from PIL import Image
# import numpy as np # Flake8 reports line 6 unused
from api import queue_manager  # Import queue_manager at the top

# Assuming your FastAPI app instance is named 'app' in 'api/api.py'
from api.api import app


# Create a TestClient instance
client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_heavy_operations(mocker):
    """
    Fixture to automatically mock heavy operations for all tests.
    - Mocks model loading to return a dummy dict.
    - Mocks the background worker task to prevent it from starting.
    - Mocks GPU/CUDA related functions to avoid errors in environments without NVIDIA drivers.
    """
    # Mock model loading
    mocker.patch.dict("api.api.loaded_models", {"model": "mocked"}, clear=True)
    # Mock background worker
    mocker.patch("api.api.background_worker_task")
    # Mock model unloading during shutdown
    mocker.patch("api.models.unload_models")

    # --- Mock GPU/CUDA related parts ---
    # Mock the gpu device object itself to avoid cuda initialization error at import time
    # Need to mock where it's defined and potentially where it's used if imported directly
    mocker.patch("diffusers_helper.memory.gpu", torch.device('cpu'))  # Use cpu device
    mocker.patch("api.models.gpu", torch.device('cpu'))  # Mock in models module too if imported there

    # Mock functions that interact with CUDA memory or device properties
    mocker.patch("diffusers_helper.memory.get_cuda_free_memory_gb", return_value=16.0)  # Return dummy high value
    mocker.patch("diffusers_helper.memory.torch.cuda.is_available", return_value=False)
    mocker.patch("diffusers_helper.memory.torch.cuda.current_device", return_value=0)  # Return dummy device index
    mocker.patch("diffusers_helper.memory.torch.cuda.empty_cache")  # Mock to do nothing
    mocker.patch("diffusers_helper.memory.torch.cuda.mem_get_info", return_value=(16 * 1024 ** 3, 16 * 1024 ** 3))
    mocker.patch("diffusers_helper.memory.torch.cuda.memory_stats", return_value={'active_bytes.all.current': 0, 'reserved_bytes.all.current': 0})

    # Mock functions that move models (they might still be called by logic we don't bypass)
    mocker.patch("diffusers_helper.memory.move_model_to_device_with_memory_preservation")
    mocker.patch("diffusers_helper.memory.offload_model_from_device_for_memory_preservation")
    mocker.patch("diffusers_helper.memory.load_model_as_complete")
    mocker.patch("diffusers_helper.memory.unload_complete_models")

    # Mock DynamicSwapInstaller methods if they interact with CUDA implicitly or cause issues
    mocker.patch("diffusers_helper.memory.DynamicSwapInstaller.install_model")
    mocker.patch("diffusers_helper.memory.DynamicSwapInstaller.uninstall_model")


# Helper function to create a dummy image for uploads
def create_dummy_image(width=100, height=50, format="PNG"):
    """Creates an in-memory dummy image file."""
    img = Image.new('RGB', (width, height), color='red')
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format=format)
    img_byte_arr.seek(0)
    return img_byte_arr
# --- Test cases will go below ---


def test_generate_job_success(mocker):
    """
    Test successful job submission via the /generate endpoint.
    Mocks the actual worker and queue manager add function.
    """
    # Mock the queue manager's add_to_queue function to return a predictable job_id
    # and prevent actual file saving/queue modification during test.
    mock_job_id = "testjob123"
    mocker.patch("api.queue_manager.add_to_queue", return_value=mock_job_id)

    # Mock the worker function so it doesn't actually run the heavy process
    mock_worker = mocker.patch("api.worker.worker")

    # Prepare dummy image data
    dummy_image_file = create_dummy_image()
    files = {'image': ('dummy.png', dummy_image_file, 'image/png')}

    # Prepare form data
    data = {
        "prompt": "A test prompt",
        "video_length": 1.0,  # Use short length for test
        "seed": 12345,
        "steps": 5,  # Use fewer steps for test
        # Add other required form fields with default or test values
        "use_teacache": True,
        "gpu_memory_preservation": 6.0,
        "cfg": 7.0,
        "gs": 1.0,
        "rs": 1.0,
        "mp4_crf": 18.0,
    }

    # Send the POST request
    response = client.post("/generate", data=data, files=files)

    # Assertions
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["job_id"] == mock_job_id
    assert response_json["message"] == "Video generation job added to queue."

    # Verify that add_to_queue was called (optional but good)
    # We need to check the arguments it was called with if we want more specific tests
    # For now, just check if it was called is implicitly done by checking the return value (job_id)
    # queue_manager.add_to_queue.assert_called_once() # Requires importing queue_manager in test

    # Verify that the worker was NOT called directly by the endpoint
    mock_worker.assert_not_called()


def test_get_status_pending(mocker):
    """Test getting status for a pending job."""
    mock_job_id = "pendingjob456"
    # Create a mock job object with pending status
    mock_job_data = queue_manager.QueuedJob(
        prompt="pending prompt",
        image_path="/fake/path.png",
        video_length=2.0,
        job_id=mock_job_id,
        seed=67890,
        use_teacache=False,
        gpu_memory_preservation=0.0,
        steps=10,
        cfg=7.0, gs=1.0, rs=1.0,
        status="pending",  # Explicitly set status
        mp4_crf=16.0,
        # Progress fields should have defaults
    )

    # Mock get_job_by_id to return our mock job data
    mocker.patch("api.queue_manager.get_job_by_id", return_value=mock_job_data)
    # Mock os.path.exists for the output file check to return False
    mocker.patch("os.path.exists", return_value=False)
    # Mock the global variable for currently processing job ID
    mocker.patch("api.api.currently_processing_job_id", None)

    # Send the GET request
    response = client.get(f"/status/{mock_job_id}")

    # Assertions
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["job_id"] == mock_job_id
    assert response_json["status"] == "pending"
    assert response_json["progress"] == 0.0  # Default progress
    assert response_json["progress_info"] == ""  # Default info

    # Verify mocks were called as expected
    queue_manager.get_job_by_id.assert_called_once_with(mock_job_id)
    # os.path.exists might be called by get_job_by_id or the endpoint, check its call
    # os.path.exists.assert_called_once() # Be more specific about the path if needed


def test_get_status_processing(mocker):
    """Test getting status for a processing job."""
    mock_job_id = "processingjob789"
    # Create a mock job object with processing status and some progress
    mock_job_data = queue_manager.QueuedJob(
        prompt="processing prompt",
        image_path="/fake/process.png",
        video_length=3.0,
        job_id=mock_job_id,
        seed=11223,
        use_teacache=True,
        gpu_memory_preservation=6.0,
        steps=20,
        cfg=7.0, gs=1.0, rs=1.0,
        status="processing",  # Set status
        mp4_crf=16.0,
        # Set some progress
        progress=55.5,
        progress_step=11,
        progress_total=20,
        progress_info="Sampling section 1/2 - Step 11/20"
    )

    # Mock get_job_by_id to return our mock job data
    mocker.patch("api.queue_manager.get_job_by_id", return_value=mock_job_data)
    # Mock os.path.exists for the output file check to return False
    mocker.patch("os.path.exists", return_value=False)
    # Mock the global variable to indicate this job IS currently processing
    mocker.patch("api.api.currently_processing_job_id", mock_job_id)

    # Send the GET request
    response = client.get(f"/status/{mock_job_id}")

    # Assertions
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["job_id"] == mock_job_id
    assert response_json["status"] == "processing"
    assert response_json["progress"] == 55.5
    assert response_json["progress_step"] == 11
    assert response_json["progress_total"] == 20
    assert response_json["progress_info"] == "Sampling section 1/2 - Step 11/20"

    # Verify mocks
    queue_manager.get_job_by_id.assert_called_once_with(mock_job_id)


def test_get_status_completed(mocker):
    """Test getting status for a completed job (output file exists)."""
    mock_job_id = "completedjobABC"
    # Mock get_job_by_id to return None (or a completed job, either works for this path)
    mocker.patch("api.queue_manager.get_job_by_id", return_value=None)
    # Mock os.path.exists for the output file check to return True
    mocker.patch("os.path.exists", return_value=True)
    # Mock the global variable for currently processing job ID
    mocker.patch("api.api.currently_processing_job_id", None)

    # Send the GET request
    response = client.get(f"/status/{mock_job_id}")

    # Assertions
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["job_id"] == mock_job_id
    assert response_json["status"] == "completed"
    assert response_json["progress"] == 100.0  # Should indicate 100%
    assert response_json["progress_info"] == "Completed"  # Should indicate completed

    # Verify mocks
    queue_manager.get_job_by_id.assert_called_once_with(mock_job_id)
    # Verify os.path.exists was called (likely with the expected output path)
    # We need api.settings to construct the exact path for a more precise check
    # mocker.patch('api.settings.OUTPUTS_DIR', './test_outputs') # Example: override settings for test
    # expected_path = os.path.join('./test_outputs', f"{mock_job_id}.mp4")
    # os.path.exists.assert_called_once_with(expected_path)


def test_get_status_not_found(mocker):
    """Test getting status for a non-existent job."""
    mock_job_id = "notfoundjobXYZ"
    # Mock get_job_by_id to return None
    mocker.patch("api.queue_manager.get_job_by_id", return_value=None)
    # Mock os.path.exists for the output file check to return False
    mocker.patch("os.path.exists", return_value=False)
    # Mock the global variable for currently processing job ID
    mocker.patch("api.api.currently_processing_job_id", None)

    # Send the GET request
    response = client.get(f"/status/{mock_job_id}")

    # Assertions
    assert response.status_code == 404
    response_json = response.json()
    assert response_json["detail"] == "Job not found"

    # Verify mocks
    queue_manager.get_job_by_id.assert_called_once_with(mock_job_id)
    # os.path.exists should also have been called
    # os.path.exists.assert_called_once() # More specific path check if needed


def test_get_result_completed(mocker):
    """Test getting the result for a completed job."""
    mock_job_id = "resultjobDEF"
    # Mock job data (status completed)
    mock_job_data = queue_manager.QueuedJob(
        prompt="completed prompt", image_path="/fake/completed.png", video_length=1.0,
        job_id=mock_job_id, seed=1, use_teacache=True, gpu_memory_preservation=0,
        steps=1, cfg=1, gs=1, rs=1, status="completed", mp4_crf=16
    )
    # Mock get_job_by_id to return the completed job
    mocker.patch("api.queue_manager.get_job_by_id", return_value=mock_job_data)
    # Mock os.path.exists for the output file check to return True
    mocker.patch("os.path.exists", return_value=True)
    # Mock os.stat to return dummy file info, preventing FileNotFoundError inside FileResponse
    dummy_stat_result = os.stat_result((
        0, 0, 0, 0, 0, 0, 1024, 0, time.time(), 0  # st_size=1024, st_mtime=now
    ))
    mocker.patch("os.stat", return_value=dummy_stat_result)
    # Mock FileResponse class itself to prevent actual file reading/sending if stat passes
    # We still mock the class to check if it was called.
    mock_file_response_cls = mocker.patch("fastapi.responses.FileResponse")

    # Send the GET request
    response = client.get(f"/result/{mock_job_id}")

    # Assertions
    assert response.status_code == 200
    # Check if FileResponse was called with expected arguments
    # Need api.settings to construct the exact path
    # expected_path = os.path.join(settings.OUTPUTS_DIR, f"{mock_job_id}.mp4")
    # mock_file_response_cls.assert_called_once_with(expected_path, media_type="video/mp4", filename=f"{mock_job_id}.mp4")
    mock_file_response_cls.assert_called_once()  # Basic check that the class was instantiated


def test_get_result_not_completed(mocker):
    """Test getting the result for a job that is not completed."""
    mock_job_id = "notyetjobGHI"
    # Mock job data (status pending)
    mock_job_data = queue_manager.QueuedJob(
        prompt="pending prompt", image_path="/fake/pending.png", video_length=1.0,
        job_id=mock_job_id, seed=1, use_teacache=True, gpu_memory_preservation=0,
        steps=1, cfg=1, gs=1, rs=1, status="pending", mp4_crf=16
    )
    # Mock get_job_by_id to return the pending job
    mocker.patch("api.queue_manager.get_job_by_id", return_value=mock_job_data)
    # Mock os.path.exists for the output file check to return False
    mocker.patch("os.path.exists", return_value=False)

    # Send the GET request
    response = client.get(f"/result/{mock_job_id}")

    # Assertions
    assert response.status_code == 404
    response_json = response.json()
    assert response_json["detail"] == f"Job '{mock_job_id}' is not completed yet (status: pending)."

    # Verify mocks
    queue_manager.get_job_by_id.assert_called_once_with(mock_job_id)
    # os.path.exists should also have been called
    # os.path.exists.assert_called_once() # More specific path check if needed


def test_get_result_not_found(mocker):
    """Test getting the result for a non-existent job."""
    mock_job_id = "neverexistedJKL"
    # Mock get_job_by_id to return None
    mocker.patch("api.queue_manager.get_job_by_id", return_value=None)
    # Mock os.path.exists for the output file check to return False
    mocker.patch("os.path.exists", return_value=False)

    # Send the GET request
    response = client.get(f"/result/{mock_job_id}")

    # Assertions
    assert response.status_code == 404
    response_json = response.json()
    # The exact error message might vary slightly, adjust if needed
    assert f"Job '{mock_job_id}' not found" in response_json["detail"]

    # Verify mocks
    queue_manager.get_job_by_id.assert_called_once_with(mock_job_id)
    # os.path.exists should also have been called
    # os.path.exists.assert_called_once() # More specific path check if needed


def test_cancel_job_success(mocker):
    """Test successfully requesting cancellation for a pending job."""
    mock_job_id = "cancellingjobMNO"
    # Mock job data (status pending)
    mock_job_data = queue_manager.QueuedJob(
        prompt="to be cancelled", image_path="/fake/cancel.png", video_length=1.0,
        job_id=mock_job_id, seed=1, use_teacache=True, gpu_memory_preservation=0,
        steps=1, cfg=1, gs=1, rs=1, status="pending", mp4_crf=16
    )
    # Mock get_job_by_id to return the pending job initially
    mocker.patch("api.queue_manager.get_job_by_id", return_value=mock_job_data)
    # Mock os.path.exists for the output file check to return False
    mocker.patch("os.path.exists", return_value=False)
    # Mock update_job_status to check if it's called correctly
    mock_update_status = mocker.patch("api.queue_manager.update_job_status", return_value=True)

    # Send the POST request
    response = client.post(f"/cancel/{mock_job_id}")

    # Assertions
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["message"] == f"Cancellation requested for job {mock_job_id}."

    # Verify that update_job_status was called with "cancelled"
    mock_update_status.assert_called_once_with(mock_job_id, "cancelled")


def test_cancel_job_already_completed(mocker):
    """Test requesting cancellation for an already completed job."""
    mock_job_id = "alreadycompletedPQR"
    # Mock job data (status completed)
    mock_job_data = queue_manager.QueuedJob(
        prompt="already done", image_path="/fake/done.png", video_length=1.0,
        job_id=mock_job_id, seed=1, use_teacache=True, gpu_memory_preservation=0,
        steps=1, cfg=1, gs=1, rs=1, status="completed", mp4_crf=16
    )
    # Mock get_job_by_id to return the completed job
    mocker.patch("api.queue_manager.get_job_by_id", return_value=mock_job_data)
    # Mock os.path.exists for the output file check (might be called, return True just in case)
    mocker.patch("os.path.exists", return_value=True)
    # Mock update_job_status to ensure it's NOT called
    mock_update_status = mocker.patch("api.queue_manager.update_job_status")

    # Send the POST request
    response = client.post(f"/cancel/{mock_job_id}")

    # Assertions
    assert response.status_code == 200  # Endpoint itself succeeds
    response_json = response.json()
    assert response_json["message"] == "Job is already completed."

    # Verify that update_job_status was NOT called
    mock_update_status.assert_not_called()


def test_cancel_job_not_found(mocker):
    """Test requesting cancellation for a non-existent job."""
    mock_job_id = "ghostjobSTU"
    # Mock get_job_by_id to return None
    mocker.patch("api.queue_manager.get_job_by_id", return_value=None)
    # Mock os.path.exists for the output file check to return False
    mocker.patch("os.path.exists", return_value=False)
    # Mock update_job_status to ensure it's NOT called
    mock_update_status = mocker.patch("api.queue_manager.update_job_status")

    # Send the POST request
    response = client.post(f"/cancel/{mock_job_id}")

    # Assertions
    assert response.status_code == 404
    response_json = response.json()
    assert response_json["detail"] == "Job not found"

    # Verify that update_job_status was NOT called
    mock_update_status.assert_not_called()


def test_get_worker_status_processing(mocker):
    """Test getting worker status when it's processing a job."""
    mock_job_id = "workerjobVWX"
    # Mock the global variables in api.api
    mocker.patch("api.api.worker_running", True)
    mocker.patch("api.api.currently_processing_job_id", mock_job_id)

    # Send the GET request
    response = client.get("/worker/status")

    # Assertions
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["is_running"] is True
    assert response_json["processing_job_id"] == mock_job_id


def test_get_worker_status_idle(mocker):
    """Test getting worker status when it's idle."""
    # Mock the global variables in api.api
    mocker.patch("api.api.worker_running", True)  # Still running, but no job
    mocker.patch("api.api.currently_processing_job_id", None)

    # Send the GET request
    response = client.get("/worker/status")

    # Assertions
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["is_running"] is True
    assert response_json["processing_job_id"] is None


def test_get_worker_status_not_running(mocker):
    """Test getting worker status when it's not running."""
    # Mock the global variables in api.api
    mocker.patch("api.api.worker_running", False)
    mocker.patch("api.api.currently_processing_job_id", None)  # Should be None if not running

    # Send the GET request
    response = client.get("/worker/status")

    # Assertions
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["is_running"] is False
    assert response_json["processing_job_id"] is None