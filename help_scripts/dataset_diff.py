import json

def load_json_file(file_path):
    with open(file_path, 'r') as f:
        return json.load(f)

FILE1 = "dataset.json"
FILE2 = "dataset.json.bak"

if __name__ == "__main__":
    data1 = load_json_file(FILE1)
    data2 = load_json_file(FILE2)

    data1_set = set(item["id"] for item in data1)
    data2_set = set(item["id"] for item in data2)

    print(f"Only in New: {sorted(list(data1_set-data2_set))}")
    print(f"Only in Old: {sorted(list(data2_set-data1_set))}")

    for data in data2:
        if data["id"] not in data1_set:
            data1.append(data)

    print(len(data1))
    # with open(FILE1, 'w') as f:
    #     json.dump(data1, f, indent=4)