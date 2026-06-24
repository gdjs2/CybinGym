import docker
import json
import os
import tarfile
import io

def main():
    # 1. A list storing all IDs of docker images to be processed
    # Add your actual target IDs to this list
    IMAGE_IDS = ["11078", "11173", "19013"] 
    
    # Initialize the Docker client
    client = docker.from_env()

    # Load the target binary information from dataset.json
    try:
        with open("dataset.json", "r") as f:
            dataset = json.load(f)
        # Create a dictionary mapping the ID to its data for easy lookup
        dataset_map = {item["id"]: item for item in dataset}
    except FileNotFoundError:
        print("Error: dataset.json not found in the current directory.")
        return
    except json.JSONDecodeError:
        print("Error: dataset.json is not a valid JSON file.")
        return

    for img_id in IMAGE_IDS:
        print(f"\n{'='*40}\nProcessing ID: {img_id}\n{'='*40}")
        
        if img_id not in dataset_map:
            print(f"Skipping: ID {img_id} not found in dataset.json")
            continue
            
        target_binary = dataset_map[img_id]["metadata"]["target_binary"]
        
        # Define host paths
        poc_dir = os.path.abspath(f"./data/{img_id}")
        poc_host_path = os.path.join(poc_dir, "poc")
        
        # Ensure the host directory exists
        os.makedirs(poc_dir, exist_ok=True)

        # 2 & 3 & 4. Create temporary instance and extract /tmp/poc
        vul_image_name = f"n132/arvo:{img_id}-vul"
        print(f"Extracting /tmp/poc from {vul_image_name}...")
        
        # We use create() instead of run() because we only need the container filesystem to copy the file
        temp_container = client.containers.create(vul_image_name)
        
        try:
            # get_archive returns a raw tar stream and stats
            stream, stat = temp_container.get_archive("/tmp/poc")
            file_obj = io.BytesIO(b"".join(chunk for chunk in stream))
            
            # Extract the file from the tarball into the host directory
            with tarfile.open(fileobj=file_obj) as tar:
                member = tar.getmember("poc")
                extracted_f = tar.extractfile(member)
                if extracted_f is None:
                    raise FileNotFoundError("The file 'poc' was not found in the tar archive.")
                with open(poc_host_path, "wb") as out_f:
                    out_f.write(extracted_f.read())
            print(f"Success: Copied /tmp/poc to {poc_host_path}")
            
        except Exception as e:
            print(f"Failed to extract /tmp/poc: {e}")
            temp_container.remove()
            continue
        finally:
            # Clean up the temporary container
            temp_container.remove()

        # Define Docker volume bindings for the PoC file
        volumes = {
            poc_host_path: {'bind': '/poc', 'mode': 'ro'}
        }

        # 5. Run vul and fix images, capture exit codes
        fix_image_name = f"n132/arvo:{img_id}-fix"
        cmd_step5 = ["/bin/sh", "-c", f"/out/{target_binary} /poc"]

        for img_name in [vul_image_name, fix_image_name]:
            print(f"\nRunning {img_name}...")
            container = client.containers.run(
                img_name,
                command=cmd_step5,
                volumes=volumes,
                detach=True # Run detached so we can wait() and grab the specific exit code
            )
            result = container.wait()
            print(f"--> [{img_name}] Exit Code: {result['StatusCode']}")
            container.remove()

        # 6. Run merge image and execute both binaries
        merge_image_name = f"lambangaw/cybingym:{img_id}-merge"
        print(f"\nRunning merge image {merge_image_name}...")
        
        # Run the container with a dummy command to keep it alive while we execute multiple commands inside it
        merge_container = client.containers.run(
            merge_image_name,
            command=["tail", "-f", "/dev/null"], 
            volumes=volumes,
            detach=True
        )

        try:
            # Execute vul binary inside the merge container
            cmd_vul = ["/bin/sh", "-c", f"/out-vul/{target_binary} /poc"]
            exec_vul = merge_container.exec_run(cmd_vul)
            print(f"--> [{merge_image_name} - VUL] Exit Code: {exec_vul.exit_code}")

            # Execute fix binary inside the merge container
            cmd_fix = ["/bin/sh", "-c", f"/out-fix/{target_binary} /poc"]
            exec_fix = merge_container.exec_run(cmd_fix)
            print(f"--> [{merge_image_name} - FIX] Exit Code: {exec_fix.exit_code}")
            
        finally:
            # Clean up the merge container
            merge_container.stop()
            merge_container.remove()

if __name__ == "__main__":
    main()