"""Debug: why does list become None during ExtractedRequirement class definition?"""
import sys
sys.path.insert(0, 'src')

# First, let's check if the issue is specifically with 'list' name in enum classes
# by looking at what comes right before ExtractedRequirement

with open('src/phase2/models.py', 'r') as f:
    lines = f.readlines()

# Print lines 130-175 (around ExtractedRequirement)
for i in range(130, min(180, len(lines))):
    print(f"{i+1:4d}: {lines[i].rstrip()}")
