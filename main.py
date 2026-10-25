from __future__ import annotations
import argparse, json, time
from dataclasses import dataclass
from pathlib import Path
import numpy as np

SEED = 20261023
N_ITEMS = 30_000
DIM = 32
N_CLUSTERS = 48
TOP_K = 10

@dataclass
class Dataset:
    vectors: np.ndarray
    category: np.ndarray
    price: np.ndarray
    zone: np.ndarray

def generate_data(n: int = N_ITEMS, dim: int = DIM, seed: int = SEED) -> Dataset:
    """Reproducible synthetic catalogue with skewed zones and correlated clusters."""
    rng = np.random.default_rng(seed)
    # A few large zones, a long tail of tiny zones.
    zone_weights = np.array([0.25, 0.18, 0.13, 0.10, 0.08, 0.06, 0.045, 0.035,
                             0.025, 0.02, 0.015, 0.012, 0.01, 0.008, 0.006])
    zone_weights = np.concatenate([zone_weights, np.repeat(0.002, 15)])
    zone_weights = zone_weights / zone_weights.sum()
    zone = rng.choice(len(zone_weights), size=n, p=zone_weights)
    # Categories 0..19. Zone 3 (Tamil Nadu-like) has a high saree share (category 0).
    category = rng.integers(0, 20, size=n)
    tn = zone == 3
    category[tn] = np.where(rng.random(tn.sum()) < 0.58, 0,
                            rng.integers(1, 20, size=tn.sum()))
    # Vector cluster centers; category and zone contribute to the signal.
    centers = rng.normal(0, 3, size=(20, dim))
    zone_offsets = rng.normal(0, 1.2, size=(len(zone_weights), dim))
    vectors = centers[category] + zone_offsets[zone] + rng.normal(0, 0.65, size=(n, dim))
    # Prices are positively skewed and vary by category.
    category_base = 250 + category * 75
    price = np.clip(rng.lognormal(np.log(category_base), 0.45), 99, 12000).astype(np.float32)
    return Dataset(vectors.astype(np.float32), category.astype(np.int16),
                   price, zone.astype(np.int16))

def l2_squared(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.sum((a - b) ** 2, axis=-1)

def exact_topk(data: Dataset, q: np.ndarray, mask: np.ndarray, k: int = TOP_K) -> np.ndarray:
    ids = np.flatnonzero(mask)
    if len(ids) == 0:
        return ids
    d = l2_squared(data.vectors[ids], q)
    return ids[np.argsort(d)[:k]]

def kmeans(vectors: np.ndarray, k: int, seed: int = SEED, iterations: int = 12):
    rng = np.random.default_rng(seed)
    # k-means++-like farthest sampling is intentionally simple and dependency-free.
    first = int(rng.integers(len(vectors)))
    chosen = [first]
    nearest = l2_squared(vectors, vectors[first])
    for _ in range(1, k):
        idx = int(np.argmax(nearest))
        chosen.append(idx)
        nearest = np.minimum(nearest, l2_squared(vectors, vectors[idx]))
    centers = vectors[chosen].copy()
    labels = np.zeros(len(vectors), dtype=np.int32)
    for _ in range(iterations):
        # Chunk rows to avoid a large temporary matrix.
        for start in range(0, len(vectors), 2048):
            block = vectors[start:start+2048]
            d = ((block[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
            labels[start:start+len(block)] = d.argmin(axis=1)
        for j in range(k):
            members = vectors[labels == j]
            if len(members):
                centers[j] = members.mean(axis=0)
    return centers.astype(np.float32), labels

class IVFIndex:
    def __init__(self, data: Dataset, nlist: int = N_CLUSTERS):
        self.data = data
        self.centers, self.labels = kmeans(data.vectors, min(nlist, len(data.vectors)))
        self.members = [np.flatnonzero(self.labels == j) for j in range(len(self.centers))]
        self.live = np.ones(len(data.vectors), dtype=bool)
        self.rebuild_metadata()

    def rebuild_metadata(self):
        d = self.data
        live_ids = np.flatnonzero(self.live)
        self.zone_counts = np.bincount(d.zone[live_ids], minlength=int(d.zone.max())+1)
        self.category_counts = np.bincount(d.category[live_ids], minlength=20)
        self.partition_zone = []
        self.partition_category = []
        for ids in self.members:
            ids = ids[self.live[ids]]
            self.partition_zone.append(set(d.zone[ids].tolist()))
            self.partition_category.append(set(d.category[ids].tolist()))

    def query_mask(self, category=None, zone=None, price_min=None, price_max=None):
        d = self.data
        mask = self.live.copy()
        if category is not None: mask &= d.category == category
        if zone is not None: mask &= d.zone == zone
        if price_min is not None: mask &= d.price >= price_min
        if price_max is not None: mask &= d.price <= price_max
        return mask

    def estimate_selectivity(self, category=None, zone=None, price_min=None, price_max=None):
        # Product of marginal selectivities. This is cheap but can be wrong for correlated predicates.
        n = max(1, int(self.live.sum()))
        est = 1.0
        if category is not None:
            est *= float(np.sum(self.live & (self.data.category == category))) / n
        if zone is not None:
            est *= float(np.sum(self.live & (self.data.zone == zone))) / n
        if price_min is not None or price_max is not None:
            p = self.data.price[self.live]
            lo = -np.inf if price_min is None else price_min
            hi = np.inf if price_max is None else price_max
            est *= float(np.mean((p >= lo) & (p <= hi))) if len(p) else 0.0
        return max(0.0, min(1.0, est))

    def plan(self, selectivity: float):
        # Initial thresholds are transparent hypotheses; benchmark output is used to revisit them.
        if selectivity <= 0.002:
            return "exact_filtered"
        if selectivity <= 0.08:
            return "partition_prefilter"
        return "ivf_postfilter"

    def search(self, q, category=None, zone=None, price_min=None, price_max=None,
               k=TOP_K, strategy="planner"):
        mask = self.query_mask(category, zone, price_min, price_max)
        estimate = self.estimate_selectivity(category, zone, price_min, price_max)
        chosen = self.plan(estimate) if strategy == "planner" else strategy
        if chosen == "exact_filtered":
            result = exact_topk(self.data, q, mask, k)
        elif chosen == "prefilter":
            ids = np.flatnonzero(mask)
            if len(ids):
                result = ids[np.argsort(l2_squared(self.data.vectors[ids], q))[:k]]
            else: result = ids
        elif chosen == "postfilter":
            result = self._ivf(q, mask, k, partition_ids=None)
        elif chosen == "partition_prefilter":
            # Restrict IVF search to partitions containing at least one matching live item.
            part_ids = []
            for j, ids in enumerate(self.members):
                if np.any(mask[ids]):
                    part_ids.append(j)
            result = self._ivf(q, mask, k, partition_ids=part_ids)
        elif chosen == "ivf_postfilter":
            result = self._ivf(q, mask, k, partition_ids=None)
        else:
            raise ValueError(f"Unknown strategy: {chosen}")
        return result, chosen, estimate

    
    
    def _ivf(self, q, mask, k, partition_ids=None):
        center_ids = (
            np.arange(len(self.centers))
            if partition_ids is None
            else np.asarray(partition_ids, dtype=int)
        )

        if len(center_ids) == 0:
            return np.array([], dtype=int)

        center_dist = l2_squared(self.centers[center_ids], q)
        order = center_ids[np.argsort(center_dist)]

        # Start with 15% of eligible partitions, then expand adaptively.
        nprobe = min(
            len(order),
            max(1, int(np.ceil(len(order) * 0.15)))
        )
        batch_size = max(1, nprobe)
        previous_ids = None

        while True:
            selected = order[:nprobe]
            raw = np.concatenate([self.members[j] for j in selected])
            candidates = raw[self.live[raw] & mask[raw]]

            if len(candidates) == 0:
                if nprobe >= len(order):
                    return candidates
                nprobe = min(len(order), nprobe + batch_size)
                continue

            distances = l2_squared(self.data.vectors[candidates], q)
            result = candidates[np.argsort(distances)[:k]]

            # If fewer than k candidates exist, search more partitions.
            if len(candidates) < k and nprobe < len(order):
                nprobe = min(len(order), nprobe + batch_size)
                continue

            # Expand once more if the result is not yet stable.
            # Stability is a practical heuristic, not a proof of exact recall.
            if previous_ids is not None and np.array_equal(
                np.sort(result), np.sort(previous_ids)
            ):
                return result

            if nprobe >= len(order):
                return result

            previous_ids = result.copy()
            nprobe = min(len(order), nprobe + batch_size)

    def delete_zone_fraction(self, zone: int, fraction: float = 0.10, seed: int = SEED+1):
        ids = np.flatnonzero(self.live & (self.data.zone == zone))
        rng = np.random.default_rng(seed)
        count = int(len(ids) * fraction)
        if count:
            self.live[rng.choice(ids, size=count, replace=False)] = False
        self.rebuild_metadata()
        return count

    def add_shifted_items(self, count: int, seed: int = SEED+2):
        # New category 20 deliberately shifts metadata distribution; rebuild index for consistent IDs.
        rng = np.random.default_rng(seed)
        d = self.data
        dim = d.vectors.shape[1]
        new_cat = np.full(count, 20, dtype=np.int16)
        new_zone = rng.integers(0, 5, size=count).astype(np.int16)
        new_price = rng.lognormal(np.log(1800), 0.5, size=count).clip(99, 12000).astype(np.float32)
        new_vectors = rng.normal(5.5, 1.0, size=(count, dim)).astype(np.float32)
        combined = Dataset(np.vstack([d.vectors, new_vectors]),
                           np.concatenate([d.category, new_cat]),
                           np.concatenate([d.price, new_price]),
                           np.concatenate([d.zone, new_zone]))
        old_live = self.live.copy()
        self.data = combined
        self.live = np.concatenate([old_live, np.ones(count, dtype=bool)])
        # Rebuild partitions after inserts; this is one possible operational response to drift.
        self.centers, self.labels = kmeans(combined.vectors, min(N_CLUSTERS, len(combined.vectors)), seed=SEED+3, iterations=8)
        self.members = [np.flatnonzero(self.labels == j) for j in range(len(self.centers))]
        self.rebuild_metadata()

def benchmark(index: IVFIndex, n_queries=100, seed=SEED + 10):
    rng = np.random.default_rng(seed)
    d = index.data
    n_live = int(index.live.sum())

    if n_live == 0:
        raise ValueError("Cannot benchmark an index with no live items.")

    # Build candidate filters. Their selectivity is measured from the
    # current live data; bucket names are never assumed to be exact.
    candidates = [dict()]

    categories = np.unique(d.category[index.live]).tolist()
    zones = np.unique(d.zone[index.live]).tolist()

    for category in categories:
        candidates.append({"category": int(category)})

    for zone in zones:
        candidates.append({"zone": int(zone)})

    # Category/zone combinations capture the correlations in the data.
    for category in categories:
        for zone in zones:
            candidates.append({
                "category": int(category),
                "zone": int(zone),
            })

    # Add price thresholds to find selective filters, including small
    # result sets. Quantiles are used to generate candidates, not to
    # claim that a filter has a particular selectivity.
    live_prices = np.asarray(d.price)[index.live]
    quantiles = np.unique(
        np.quantile(
            live_prices,
            [0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.95, 0.98],
        )
    )

    for threshold in quantiles:
        candidates.append({"price_max": float(threshold)})

        for zone in zones:
            candidates.append({
                "zone": int(zone),
                "price_max": float(threshold),
            })

        for category in categories:
            candidates.append({
                "category": int(category),
                "price_max": float(threshold),
            })

    # Add three-predicate combinations to find very small result sets.
    for category in categories:
        for zone in zones:
            for threshold in quantiles:
                candidates.append({
                    "category": int(category),
                    "zone": int(zone),
                    "price_max": float(threshold),
                })

    # Deduplicate candidate filters.
    unique_candidates = {}
    for filters in candidates:
        key = tuple(sorted(filters.items()))
        unique_candidates[key] = filters
    candidates = list(unique_candidates.values())

    # Measure exact counts for candidate selection. This full scan is
    # benchmark setup, not part of the timed search operation.
    measured = []
    for filters in candidates:
        mask = index.query_mask(**filters) & index.live
        count = int(mask.sum())
        if count > 0:
            measured.append({
                "filters": filters,
                "mask": mask,
                "matches": count,
                "selectivity": count / n_live,
            })

    if not measured:
        raise ValueError("No non-empty filter candidates were found.")

    def closest_to_count(target_count, used_keys):
        available = [
            item for item in measured
            if tuple(sorted(item["filters"].items())) not in used_keys
        ]
        if not available:
            available = measured

        return min(
            available,
            key=lambda item: abs(
                np.log(max(item["matches"], 1) / max(target_count, 1))
            ),
        )

    specs = []
    used_keys = set()

    # The unfiltered baseline is exactly 100% of live items.
    all_items = next(
        item for item in measured if not item["filters"]
    )
    specs.append(("~100%", all_items))
    used_keys.add(tuple(sorted(all_items["filters"].items())))

    # Choose filters by their measured counts, not their names.
    for label, fraction in [
        ("~10%", 0.10),
        ("~1%", 0.01),
        ("~0.1%", 0.001),
    ]:
        item = closest_to_count(n_live * fraction, used_keys)
        specs.append((label, item))
        used_keys.add(tuple(sorted(item["filters"].items())))

    # Prefer a real handful of matches (1–10). If none exists, use
    # the smallest non-empty candidate and report its actual count.
    small_candidates = [
        item for item in measured
        if 1 <= item["matches"] <= 10
    ]
    if small_candidates:
        handful = min(small_candidates, key=lambda item: item["matches"])
    else:
        handful = min(measured, key=lambda item: item["matches"])

    specs.append(("handful", handful))

    strategies = ["planner", "postfilter", "prefilter"]
    rows = []

    # Use independent perturbed vectors so the query is not an exact
    # copy of a stored catalogue vector.
    live_ids = np.flatnonzero(index.live)

    for bucket, item in specs:
        filters = item["filters"]
        mask = item["mask"]
        pool = np.flatnonzero(mask)

        query_ids = rng.choice(
            live_ids,
            size=n_queries,
            replace=len(live_ids) < n_queries,
        )

        queries = []
        for qi in query_ids:
            q = np.asarray(d.vectors[qi], dtype=np.float32).copy()
            q += rng.normal(0.0, 0.05, size=q.shape).astype(np.float32)
            queries.append(q)

        for strategy in strategies:
            recalls = []
            latencies = []
            chosen = []

            for q in queries:
                # Exact ground truth over every live item satisfying
                # the filter. Ground truth computation is not timed.
                gt = exact_topk(d, q, mask, TOP_K)

                start = time.perf_counter()
                result, plan, _ = index.search(
                    q, **filters, strategy=strategy
                )
                latencies.append((time.perf_counter() - start) * 1000)
                chosen.append(plan)

                denominator = min(TOP_K, len(gt))
                if denominator == 0:
                    recalls.append(1.0)
                else:
                    recalls.append(
                        len(set(gt.tolist()) & set(result.tolist()))
                        / denominator
                    )

            rows.append({
                "bucket": bucket,
                "filters": str(filters),
                "matches": item["matches"],
                "selectivity": round(item["selectivity"], 6),
                "strategy": strategy,
                "recall_at_10": round(float(np.mean(recalls)), 4),
                "p50_ms": round(float(np.percentile(latencies, 50)), 3),
                "p95_ms": round(float(np.percentile(latencies, 95)), 3),
                "chosen": max(set(chosen), key=chosen.count),
            })

    return rows

def print_table(rows):
    print("bucket | matches | strategy | recall@10 | p50 ms | p95 ms | representative plan")
    print("--- | ---: | --- | ---: | ---: | ---: | ---")
    for r in rows:
        print(f"{r['bucket']} | {r['matches']} | {r['strategy']} | {r['recall_at_10']} | "
              f"{r['p50_ms']} | {r['p95_ms']} | {r['chosen']}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["benchmark", "mutation", "demo"])
    parser.add_argument("--n", type=int, default=N_ITEMS)
    args = parser.parse_args()
    data = generate_data(n=args.n)
    print(f"Generated {len(data.vectors):,} vectors, dimension={data.vectors.shape[1]}, seed={SEED}")
    t0 = time.perf_counter()
    index = IVFIndex(data)
    print(f"Built IVF index with {len(index.centers)} partitions in {time.perf_counter()-t0:.2f}s")
    if args.command == "benchmark":
        rows = benchmark(index)
        print_table(rows)
        Path("results").mkdir(exist_ok=True)
        Path("results/benchmark.json").write_text(json.dumps(rows, indent=2))
        print("Saved results/benchmark.json")
    elif args.command == "mutation":
        busiest = int(np.argmax(index.zone_counts))
        deleted = index.delete_zone_fraction(busiest, 0.10)
        added = int(round(len(index.data.vectors) * 0.05))
        index.add_shifted_items(added)
        print(f"Deleted {deleted} items from busiest zone {busiest}; added {added} shifted items.")
        rows = benchmark(index)
        print_table(rows)
        Path("results").mkdir(exist_ok=True)
        Path("results/mutation.json").write_text(json.dumps(rows, indent=2))
        print("Saved results/mutation.json")
    else:
        qid = 0
        q = data.vectors[qid]
        for filt in [dict(), dict(zone=0), dict(category=0, zone=3), dict(zone=17)]:
            _, chosen, est = index.search(q, **filt)
            print(f"filters={filt} estimated_selectivity={est:.4f} -> {chosen}")


def diagnose_recall():
    data = generate_data(n=30000, seed=SEED)
    index = IVFIndex(data)
    rng = np.random.default_rng(SEED + 10)

    # Use the same approximate 10% price filter as the benchmark.
    price_max = float(np.quantile(data.price, 0.10))
    filters = {"price_max": price_max}
    mask = index.query_mask(**filters) & index.live

    live_ids = np.flatnonzero(index.live)
    query_ids = rng.choice(
        live_ids, size=min(100, len(live_ids)), replace=False
    )

    total_hits = 0
    total_expected = 0
    bad_ids = 0

    for qi in query_ids:
        q = data.vectors[qi].copy()
        q += rng.normal(0.0, 0.05, size=q.shape).astype(np.float32)

        truth = exact_topk(data, q, mask, TOP_K)
        result, _, _ = index.search(q, **filters, strategy="planner")

        if not np.all(mask[result]):
            bad_ids += 1

        total_hits += len(set(truth.tolist()) & set(result.tolist()))
        total_expected += min(TOP_K, len(truth))

    recall = total_hits / max(total_expected, 1)
    print(f"Filter matches: {int(mask.sum())}")
    print(f"Measured selectivity: {mask.mean():.6f}")
    print(f"Aggregate recall@10: {recall:.4f}")
    print(f"Results outside filter: {bad_ids}")



def diagnose_nprobe():
    data = generate_data(n=30000, seed=SEED)
    index = IVFIndex(data)
    rng = np.random.default_rng(SEED + 10)

    price_max = float(np.quantile(data.price, 0.10))
    mask = index.query_mask(price_max=price_max) & index.live

    query_ids = rng.choice(
        np.flatnonzero(index.live), size=100, replace=False
    )

    # Generate queries once so every probing percentage uses identical inputs.
    queries = []
    for qi in query_ids:
        q = data.vectors[qi].copy()
        q += rng.normal(0.0, 0.05, size=q.shape).astype(np.float32)
        queries.append(q)

    truths = [exact_topk(data, q, mask, TOP_K) for q in queries]

    print("partitions | recall@10")
    for fraction in [0.15, 0.25, 0.50, 0.75, 1.0]:
        hits = 0
        expected = 0

        for q, truth in zip(queries, truths):
            order = np.argsort(l2_squared(index.centers, q))
            nprobe = max(1, int(np.ceil(len(order) * fraction)))
            raw = np.concatenate(
                [index.members[j] for j in order[:nprobe]]
            )
            candidates = raw[index.live[raw] & mask[raw]]

            if len(candidates):
                distances = l2_squared(data.vectors[candidates], q)
                result = candidates[np.argsort(distances)[:TOP_K]]
            else:
                result = np.array([], dtype=int)

            hits += len(set(truth.tolist()) & set(result.tolist()))
            expected += min(TOP_K, len(truth))

        recall = hits / max(expected, 1)
        print(f"{fraction:>9.0%} | {recall:.4f}")



if __name__ == "__main__":
    main()
