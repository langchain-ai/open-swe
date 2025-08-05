#!/usr/bin/env python3
"""
Script to fetch and display SWE-bench dataset examples
"""

from datasets import load_dataset
import json


def fetch_swebench_example(dataset_name='princeton-nlp/SWE-bench', split='test', index=0):
    """
    Fetch a single example from SWE-bench dataset
    
    Args:
        dataset_name: HuggingFace dataset name
        split: Dataset split ('test', 'train', 'dev')
        index: Index of example to fetch
    
    Returns:
        dict: Single SWE-bench example
    """
    print(f"Loading {dataset_name} dataset...")
    dataset = load_dataset(dataset_name, split=split)
    
    print(f"Dataset size: {len(dataset)} examples")
    
    if index >= len(dataset):
        print(f"Index {index} out of range. Max index: {len(dataset) - 1}")
        return None
    
    example = dataset[index]
    return example


def display_example(example, truncate_patch=True):
    """
    Display a SWE-bench example in a readable format
    
    Args:
        example: SWE-bench dataset example
        truncate_patch: Whether to truncate long patches
    """
    if not example:
        return
    
    print("\n" + "="*80)
    print("SWE-BENCH EXAMPLE")
    print("="*80)
    
    # Display key fields
    key_fields = ['repo', 'instance_id', 'base_commit', 'created_at', 'version']
    
    for field in key_fields:
        if field in example:
            print(f"{field.upper()}: {example[field]}")
    
    print(f"\nPROBLEM STATEMENT:")
    print("-" * 50)
    print(example.get('problem_statement', 'N/A'))
    
    print(f"\nPATCH:")
    print("-" * 50)
    patch = example.get('patch', 'N/A')
    if truncate_patch and len(patch) > 1000:
        print(patch[:1000] + "\n... [TRUNCATED - Full patch is longer] ...")
    else:
        print(patch)
    
    # Show all available fields
    print(f"\nAVAILABLE FIELDS:")
    print("-" * 50)
    for key in example.keys():
        value_preview = str(example[key])[:100]
        if len(str(example[key])) > 100:
            value_preview += "..."
        print(f"  {key}: {value_preview}")


def save_example_to_json(example, filename):
    """Save example to JSON file"""
    with open(filename, 'w') as f:
        json.dump(example, f, indent=2, default=str)
    print(f"\nExample saved to: {filename}")


def fetch_multiple_examples(count=100, dataset_name='princeton-nlp/SWE-bench', split='test'):
    """
    Fetch multiple examples from SWE-bench dataset
    
    Args:
        count: Number of examples to fetch
        dataset_name: HuggingFace dataset name
        split: Dataset split ('test', 'train', 'dev')
    
    Returns:
        list: List of SWE-bench examples
    """
    print(f"Loading {dataset_name} dataset...")
    dataset = load_dataset(dataset_name, split=split)
    
    print(f"Dataset size: {len(dataset)} examples")
    
    if count > len(dataset) or count == float('inf'):
        print(f"Fetching all {len(dataset)} available examples.")
        count = len(dataset)
    
    examples = []
    print(f"Fetching {count} examples...")
    
    for i in range(count):
        if i % 10 == 0:
            print(f"Progress: {i}/{count} examples fetched")
        examples.append(dataset[i])
    
    print(f"Successfully fetched {len(examples)} examples")
    return examples


def save_examples_to_json(examples, filename):
    """Save multiple examples to JSON file"""
    with open(filename, 'w') as f:
        json.dump(examples, f, indent=2, default=str)
    print(f"\n{len(examples)} examples saved to: {filename}")


def main():
    """Main function to fetch all SWE-bench examples"""
    try:
        # Fetch all examples from test set
        examples = fetch_multiple_examples(count=float('inf'))
        
        if examples:
            # Save all examples to JSON file
            save_examples_to_json(examples, 'swebench_all_examples.json')
            
            # Display summary of first few examples
            print(f"\n\nSummary of first 5 examples:")
            print("="*80)
            for i, example in enumerate(examples[:5]):
                print(f"\nExample {i}:")
                print(f"  Repo: {example['repo']}")
                print(f"  Instance: {example['instance_id']}")
                print(f"  Problem: {example['problem_statement'][:100]}...")
            
            print(f"\nTotal examples fetched: {len(examples)}")
            print("All examples saved to 'swebench_all_examples.json'")
        
    except Exception as e:
        print(f"Error: {e}")
        print("Make sure you have the datasets library installed:")
        print("pip install datasets")


if __name__ == "__main__":
    main()