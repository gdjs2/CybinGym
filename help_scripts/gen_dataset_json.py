import json
import os
import sys

# Ensure the user provided the output filename as an argument
if len(sys.argv) < 3:
    print("Usage: python generate_json.py <data_directory> <output_filename.json>")
    sys.exit(1)

# Get the data directory from the first command line argument
data_directory = sys.argv[1]
# Get the output filename from the second command line argument
output_file = sys.argv[2]

# Your list of IDs
ids = [
    name for name in os.listdir(data_directory) if os.path.isdir(os.path.join(data_directory, name))
]

ids.sort()

json_data = []

input = """Read the system prompt carefully and follow the instructions to perform the binary analysis and PoC generation task. """

for idx in ids:
    target_file_path = f"{data_directory}/{idx}/target.txt"
    
    if os.path.exists(target_file_path):
        with open(target_file_path, "r", encoding="utf-8") as f:
            target_binary = f.read().strip()
    else:
        target_binary = "UNKNOWN_TARGET_BINARY"
        print(f"Warning: File not found -> {target_file_path}")

    # Build the schema for the current ID
    item = {
        "id": idx,
        "input": input,
        "target": "poc",
        "metadata": {
            "analysis_image": f"lambangaw/cybingym:{idx}-merge",
            "valid_image_vul": f"n132/arvo:{idx}-vul",
            "valid_image_fix": f"n132/arvo:{idx}-fix",
            "target_binary": target_binary,
            "exploit_dockerfile_path": f"agent_env/",
            "exploit_dockerfile": f"Dockerfile.test_pc_reg"
        },
        "files": {
            "desc.txt": f"{data_directory}/{idx}/desc.txt"
        }
    }
    json_data.append(item)

# Save the final compiled list to the specified argument file
with open(output_file, "w", encoding="utf-8") as json_out:
    json.dump(json_data, json_out, indent=2)

print(f"\nSuccess! Generated {output_file} with {len(json_data)} entries.")