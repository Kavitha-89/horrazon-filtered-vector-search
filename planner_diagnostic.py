import time
import numpy as np

from main import (
    SEED, TOP_K, IVFIndex, generate_data, exact_topk
)

def main():
    data = generate_data(n=30000, seed=SEED)
    index = IVFIndex(data)

    # A deterministic filter selecting approximately 10% of records.
    price_max = float(np.quantile(data.price, 0.10))
    filters = {"price_max": price_max}
    mask = index.query_mask(**filters) & index.live

    rng = np.random.default_rng(SEED + 10)
    live_ids = np.flatnonzero(index.live)
    query_ids = rng.choice(
        live_ids, size=min(100, len(live_ids)), replace=False
    )

    queries = []
    truths = []

    for qi in query_ids:
        q = data.vectors[qi].copy()
        q += rng.normal(0.0, 0.05, size=q.shape).astype(np.float32)
        queries.append(q)
        truths.append(exact_topk(data, q, mask, TOP_K))

    print(f"Filters: {filters}")
    print(f"Actual matching records: {int(mask.sum())}")
    print(f"Actual selectivity: {mask.mean():.4%}")
    print(f"Estimated selectivity: {index.estimate_selectivity(**filters):.4%}")
    print()
    print("strategy | recall@10 | p50 ms | p95 ms | planner choice")

    for strategy in ["planner", "prefilter", "postfilter"]:
        recalls = []
        latencies = []
        choices = []

        # Warm up each strategy before recording measurements.
        for q in queries[:5]:
            index.search(q, **filters, strategy=strategy)

        for q, truth in zip(queries, truths):
            start = time.perf_counter()
            result, chosen, _ = index.search(
                q, **filters, strategy=strategy
            )
            latencies.append((time.perf_counter() - start) * 1000)
            choices.append(chosen)

            denominator = min(TOP_K, len(truth))
            recall = (
                len(set(truth.tolist()) & set(result.tolist()))
                / denominator
                if denominator else 1.0
            )
            recalls.append(recall)

        print(
            f"{strategy} | {np.mean(recalls):.4f} | "
            f"{np.median(latencies):.3f} | "
            f"{np.percentile(latencies, 95):.3f} | "
            f"{max(set(choices), key=choices.count)}"
        )

if __name__ == "__main__":
    main()
