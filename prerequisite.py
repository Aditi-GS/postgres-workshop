from pathlib import Path
import os
import glob
import pyarrow.parquet as pq  

def merge_parquet():
    scripts_dir = Path(__file__).resolve().parent / "data"
    output_name = "yellow_tripdata_2025.parquet"
    output_file = os.path.join(scripts_dir, output_name)
    parquet_files = sorted(glob.glob(str(scripts_dir / "*.parquet"))) 

    # Skip if merged file already exists
    if os.path.exists(output_file):
        print(f"'{output_file}' already exists. Skipping merge.")
        return

    # Fetch all .parquet files from the directory
    parquet_files = sorted(glob.glob(os.path.join(scripts_dir, "*.parquet")))

    if not parquet_files:
        print(f"No .parquet files found in '{scripts_dir}'.")
        return

    print(f"Found {len(parquet_files)} files:")
    for f in parquet_files:
        print(f"  {os.path.basename(f)}")

    # Merge using PyArrow (memory-efficient, streams row groups)
    schema = pq.ParquetFile(parquet_files[0]).schema_arrow

    with pq.ParquetWriter(output_file, schema=schema) as writer:
        for f in parquet_files:
            writer.write_table(pq.read_table(f, schema=schema))

    print(f"Merged into '{output_file}'")

merge_parquet()    