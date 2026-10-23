import numpy as np
from main import Dataset, IVFIndex, generate_data, exact_topk

def test_data_reproducible():
    a = generate_data(n=500, seed=42)
    b = generate_data(n=500, seed=42)
    assert np.array_equal(a.category, b.category)
    assert np.array_equal(a.zone, b.zone)
    assert np.allclose(a.vectors, b.vectors)

def test_filter_mask_and_exact_recall():
    d = generate_data(n=800, seed=7)
    idx = IVFIndex(d, nlist=12)
    mask = idx.query_mask(category=0)
    assert np.all(d.category[np.flatnonzero(mask)] == 0)
    q = d.vectors[np.flatnonzero(mask)[0]]
    result, strategy, _ = idx.search(q, category=0, strategy="prefilter")
    truth = exact_topk(d, q, mask)
    assert set(result.tolist()) == set(truth.tolist())
    assert strategy == "prefilter"

def test_planner_returns_known_strategy():
    d = generate_data(n=600, seed=11)
    idx = IVFIndex(d, nlist=10)
    assert idx.plan(0.0001) == "exact_filtered"
    assert idx.plan(0.02) == "partition_prefilter"
    assert idx.plan(0.5) == "ivf_postfilter"

def test_delete_marks_items_inactive():
    d = generate_data(n=1000, seed=13)
    idx = IVFIndex(d, nlist=8)
    zone = int(np.argmax(idx.zone_counts))
    before = int(idx.live.sum())
    removed = idx.delete_zone_fraction(zone, .1)
    assert removed >= 0
    assert int(idx.live.sum()) == before - removed
