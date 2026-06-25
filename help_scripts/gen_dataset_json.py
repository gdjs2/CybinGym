import json
import os
import sys

# Ensure the user provided the output filename as an argument
if len(sys.argv) < 2:
    print("Error: Missing output filename.")
    print("Usage: python generate_json.py <output_filename.json>")
    sys.exit(1)

# Get the filename from the first command line argument
output_file = sys.argv[1]

# Your list of IDs
ids = [
    1856, 10013, 10055, 10096, 10147, 10252, 10306, 10341, 10400, 10574,
    10628, 10653, 10710, 10731, 10841, 10863, 10864, 10865, 10882, 10999
]

json_data = []

for idx in ids:
    target_file_path = f"./data/{idx}/target.txt"
    
    if os.path.exists(target_file_path):
        with open(target_file_path, "r", encoding="utf-8") as f:
            target_binary = f.read().strip()
    else:
        target_binary = "UNKNOWN_TARGET_BINARY"
        print(f"Warning: File not found -> {target_file_path}")

    # Build the schema for the current ID
    item = {
        "id": idx,
        "input": "Follow the system prompt.",
        "target": "poc",
        "metadata": {
            "analysis_image": f"lambangaw/cybingym:{idx}-merge",
            "valid_image_vul": f"n132/arvo:{idx}-vul",
            "valid_image_fix": f"n132/arvo:{idx}-fix",
            "target_binary": target_binary
        },
        "files": {
            "desc.txt": f"data/{idx}/desc.txt"
        }
    }
    json_data.append(item)

# Save the final compiled list to the specified argument file
with open(output_file, "w", encoding="utf-8") as json_out:
    json.dump(json_data, json_out, indent=2)

print(f"\nSuccess! Generated {output_file} with {len(json_data)} entries.")