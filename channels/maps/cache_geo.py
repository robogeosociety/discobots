#!/usr/bin/env python3
"""Build the transit-geo embedding index over the walksheds POIs (one-time / refresh).
Run from the workspace: python3 cache_geo.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import geo

t = time.time()
n = geo.build_index()
print("geo index built: %d POIs in %ds -> %s" % (n, int(time.time() - t), geo.GEO_DIR))
