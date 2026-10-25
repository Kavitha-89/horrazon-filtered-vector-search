# The Madurai Pincode Search

## 1. Problem Summary

The Madurai Pincode Search project explores filtered vector search over a synthetic marketplace dataset containing vector embeddings and metadata.

The goal is to retrieve the nearest matching vectors while supporting metadata filters and balancing search quality with query latency.

The implementation supports these filters:

- Category equality
- Delivery-zone equality
- Minimum and maximum price conditions
- Combinations of filters using AND logic

The project uses a custom Inverted File (IVF)-style vector index and a query planner that selects a search strategy based on estimated filter selectivity.

The evaluation compares the planner with pre-filter and post-filter baselines using Recall@10 and latency measurements across different filter-selectivity buckets.

## 2. Architecture

The architecture consists of five main stages:

1. **Dataset generation:** Creates synthetic vectors and metadata using a fixed random seed.
2. **Index construction:** Organizes vectors into IVF-style partitions.
3. **Filter estimation:** Estimates how many records will match the requested metadata conditions.
4. **Query planning and retrieval:** Selects a search strategy and retrieves candidate vectors.
5. **Evaluation:** Ranks results by vector distance and measures recall and latency.

The architecture diagram is included in `docs/architecture.png`.

![Architecture diagram](docs/architecture.png)

```mermaid
flowchart TD
    A["Synthetic Dataset Generator"] --> B["Vectors and Metadata"]
    B --> C["Custom IVF Index"]
    B --> D["Metadata Filter Evaluation"]
    E["Query Vector and Filters"] --> F["Selectivity Estimator"]
    F --> G["Query Planner"]
    C --> G
    D --> G
    G --> H["Selected Search Strategy"]
    H --> I["Candidate Retrieval"]
    I --> J["Distance Ranking and Top-k Results"]
    J --> K["Recall and Latency Evaluation"]
```

The planner uses estimated selectivity to choose a search path. The selected strategy applies metadata filtering and retrieves candidates, after which the results are ranked by vector distance. The benchmark compares the resulting recall and latency with the baseline methods.

## 3. Requirements and Setup

### Environment

The project was developed and tested in the following environment:

- Operating system: Windows
- Python: 3.13.11
- Git: 2.52.0.windows.1
- Numerical computation: NumPy

### Installation

Open PowerShell and navigate to the repository directory:

```powershell
cd path\to\horrazon-filtered-vector-search
```

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Install the project dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run the tests:

```powershell
python -m pytest -q
```

The latest recorded test run completed with **4 passing tests**.

## 4. Dataset

The project uses a synthetic dataset generated with a fixed random seed.

| Property | Value |
|---|---:|
| Original records | 30,000 |
| Vector dimensions | 32 |
| Categories | 20 |
| Delivery zones | Approximately 30 |
| Random seed | `20261023` |

Each record contains a vector and associated metadata. The generator introduces nonuniform distributions and relationships between some metadata fields and vector groups.

Prices are generated synthetically using category-dependent distributions. The fixed seed makes the original dataset reproducible when the same generation procedure and dependencies are used.

The dataset is intended to evaluate search behavior under different filter selectivities. It is synthetic and should not be treated as representative of every real-world marketplace workload.

## 5. Running the Project

Run these commands from the repository root.

### Run the demonstration

```powershell
python main.py demo
```

### Run the benchmark

```powershell
python main.py benchmark
```

### Run the mutation experiment

```powershell
python main.py mutation
```

### Run automated tests

```powershell
python -m pytest -q
```

The demo exercises the search workflow. The benchmark compares the planner with the baseline methods, while the mutation experiment evaluates behavior after the configured dataset changes.

Benchmark and mutation results are saved under the `results` directory.

## 6. Index and Query Planner

### Custom IVF-style index

The implementation organizes vectors into partitions around cluster centers. Instead of always comparing a query against every vector, the search process can examine selected partitions to retrieve candidate vectors.

The number of partitions examined affects the trade-off between retrieval cost and recall. Searching more partitions can improve the chance of finding the exact nearest neighbors but may increase the work performed per query.

### Search strategies

**Exact filtered search — `exact_filtered`**

Applies the metadata filters and performs exact distance ranking over the eligible records. This is intended for queries where the matching subset is sufficiently small.

**Partition prefilter — `partition_prefilter`**

Uses metadata filtering and eligible partitions to narrow the search space before ranking candidate vectors.

**IVF postfilter — `ivf_postfilter`**

Retrieves candidates from IVF partitions and applies the metadata filters to those candidates. Its effectiveness depends on the number and quality of candidates retrieved.

**Post-filter baseline — `postfilter`**

Retrieves vector candidates before applying metadata filters. This provides a comparison for filtering after candidate retrieval.

**Pre-filter baseline — `prefilter`**

Restricts the eligible records using metadata filters before vector ranking. This provides a comparison for filtering before ranking.

### Selectivity estimation

The selectivity estimator combines marginal selectivity estimates for the supported metadata predicates. The resulting estimate is used by the planner to choose a search strategy.

The configured thresholds are:

| Estimated selectivity | Selected strategy |
|---|---|
| At or below `0.002` | `exact_filtered` |
| Greater than `0.002` and at or below `0.08` | `partition_prefilter` |
| Greater than `0.08` | `ivf_postfilter` |

These thresholds are specific to this implementation and workload. They are not universal optimal values. Their effectiveness must be assessed using measured performance.

A limitation of multiplying marginal estimates is that the result can be inaccurate when metadata fields are correlated. For example, category and delivery zone may not be independent, so the estimated selectivity may differ from the actual proportion of matching records.

## 7. Benchmark Methodology

The benchmark evaluates five selectivity buckets:

- Approximately 100%
- Approximately 10%
- Approximately 1%
- Approximately 0.1%
- A handful of matching records

For each bucket, the planner is compared with the post-filter and pre-filter baselines.

**Recall@10** measures the overlap between the returned top-10 results and the exact ground-truth top-10 results under the benchmark's evaluation procedure.

**p50 latency** is the median measured query latency.

**p95 latency** is the latency below which 95% of the measured queries fall.

Latency is reported in milliseconds. The measurements represent the tested environment and query workload; they are not production performance guarantees.

The ground-truth calculation used for benchmark evaluation is separate from the timed search operation. The reported results should be interpreted in the context of the synthetic dataset and benchmark implementation.

## 8. Benchmark Results

The following measurements are from the latest recorded execution of `python main.py benchmark`. Latency is reported in milliseconds.

| Bucket | Matches | Method | Recall@10 | p50 (ms) | p95 (ms) |
|---|---:|---|---:|---:|---:|
| ~100% | 30,000 | Planner | 1.000 | 3.748 | 5.715 |
| ~100% | 30,000 | Post-filter | 1.000 | 3.085 | 3.896 |
| ~100% | 30,000 | Pre-filter | 1.000 | 5.183 | 7.228 |
| ~10% | 3,000 | Planner | 0.999 | 0.437 | 1.164 |
| ~10% | 3,000 | Post-filter | 0.999 | 0.441 | 0.804 |
| ~10% | 3,000 | Pre-filter | 1.000 | 0.293 | 0.426 |
| ~1% | 301 | Planner | 1.000 | 0.252 | 0.358 |
| ~1% | 301 | Post-filter | 1.000 | 0.242 | 0.400 |
| ~1% | 301 | Pre-filter | 1.000 | 0.133 | 0.161 |
| ~0.1% | 30 | Planner | 1.000 | 0.070 | 0.081 |
| ~0.1% | 30 | Post-filter | 1.000 | 0.153 | 0.241 |
| ~0.1% | 30 | Pre-filter | 1.000 | 0.070 | 0.097 |
| Handful | 1 | Planner | 1.000 | 0.067 | 0.085 |
| Handful | 1 | Post-filter | 1.000 | 0.313 | 0.453 |
| Handful | 1 | Pre-filter | 1.000 | 0.067 | 0.080 |

### Observations

- The planner recorded Recall@10 of at least `0.999` across all five benchmark buckets.
- The planner selected different strategies according to the estimated filter selectivity.
- Latency varied with the number of matching records and the retrieval strategy.
- The pre-filter and post-filter baselines provide comparisons for assessing the planner's behavior.
- In the broad-filter bucket, the post-filter baseline was faster than the planner in this recorded run.
- For highly selective queries, the planner was faster than the post-filter baseline in the recorded results.

The results show that no single approach should be assumed to be fastest across every workload. The planner's performance depends on the selected strategy, filter selectivity, and the overhead of the retrieval path.

## 9. Partition-Probing Experiment

An additional experiment evaluated Recall@10 at different fractions of IVF partitions examined. The same 100 queries were used for each probing percentage.

| Partitions examined | Recall@10 |
|---:|---:|
| 15% | 0.9430 |
| 25% | 0.9850 |
| 50% | 1.0000 |
| 75% | 1.0000 |
| 100% | 1.0000 |

### Observations

- At 15% probing, Recall@10 was `0.9430`, below the `0.95` target.
- At 25% probing, Recall@10 increased to `0.9850`.
- At 50% probing and above, Recall@10 was `1.0000` on the evaluated workload.

These results demonstrate the recall trade-off associated with the number of partitions examined. The 25% setting was the lowest tested percentage to meet the target in this experiment. Latency was not measured by this diagnostic, so these results do not establish that 25% is the optimal operating point.

The diagnostic is implemented by `diagnose_nprobe()` in `main.py`. In the current CLI, this function is not exposed as a command. To reproduce the table, invoke the diagnostic using a temporary entry point, then restore the normal `main()` entry point before running the regular CLI commands.

## 10. Mutation Experiment

The mutation experiment changed the dataset distribution by:

- Deleting 759 items from the busiest zone.
- Adding 1,500 shifted items.

The recorded output confirms the deletion and addition counts. The precise distribution changes should be interpreted according to the mutation logic implemented in `main.py`.

### Results after mutation

The following measurements are from the latest recorded execution of `python main.py mutation`.

| Bucket | Matches | Method | Recall@10 | p50 (ms) | p95 (ms) |
|---|---:|---|---:|---:|---:|
| ~100% | 30,741 | Planner | 1.000 | 3.749 | 6.279 |
| ~100% | 30,741 | Post-filter | 1.000 | 3.430 | 5.506 |
| ~100% | 30,741 | Pre-filter | 1.000 | 5.163 | 6.954 |
| ~10% | 3,075 | Planner | 1.000 | 0.421 | 0.947 |
| ~10% | 3,075 | Post-filter | 1.000 | 0.416 | 0.747 |
| ~10% | 3,075 | Pre-filter | 1.000 | 0.308 | 0.692 |
| ~1% | 307 | Planner | 1.000 | 0.315 | 0.573 |
| ~1% | 307 | Post-filter | 1.000 | 0.448 | 0.773 |
| ~1% | 307 | Pre-filter | 1.000 | 0.178 | 0.459 |
| ~0.1% | 31 | Planner | 1.000 | 0.088 | 0.198 |
| ~0.1% | 31 | Post-filter | 0.973 | 0.271 | 0.669 |
| ~0.1% | 31 | Pre-filter | 1.000 | 0.079 | 0.099 |
| Handful | 1 | Planner | 1.000 | 0.076 | 0.114 |
| Handful | 1 | Post-filter | 1.000 | 0.350 | 0.520 |
| Handful | 1 | Pre-filter | 1.000 | 0.076 | 0.087 |

### Observations

The planner recorded Recall@10 of `1.000` across all five mutation buckets in the latest run. The post-filter baseline recorded `0.973` in the approximately 0.1% bucket, which remains above the `0.95` target.

The mutation experiment evaluates search behavior after the configured dataset changes. Latency and recall may vary across strategies after the distribution changes.

More experiments using different mutation seeds and query sets would be needed to establish how consistently these observations hold.

## 11. Measured Planner Limitation and Failure Case

One measured limitation is that the planner does not always choose the fastest strategy.

In the original benchmark's approximately 100% selectivity bucket, all 30,000 records matched the filter. Under the configured thresholds, the planner selected `ivf_postfilter` for this broad-filter case.

The latest recorded planner measurements are:

| Metric | Planner |
|---|---:|
| Matching records | 30,000 |
| Observed selectivity | 100% |
| Recall@10 | 1.000 |
| p50 latency | 3.748 ms |
| p95 latency | 5.715 ms |

For comparison, the post-filter baseline recorded p50 latency of `3.085 ms` and p95 latency of `3.896 ms`, with Recall@10 of `1.000`.

The planner's p50 latency was approximately 21.5% higher than the post-filter baseline in this run.

This is a measured performance limitation rather than a recall failure. It shows that the configured strategy thresholds do not guarantee the lowest latency for every workload.

A related design limitation is that multiplying marginal selectivity estimates can misestimate combined-filter selectivity when metadata fields are correlated. This may lead the planner to select a suboptimal strategy.

**Follow-up work:** Compare the strategies across additional broad-filter queries, evaluate whether the post-filter path should be selected more often for broad filters, and recalibrate the thresholds using repeated measurements. Any threshold change should be validated against both latency and recall before being adopted.

The broad-filter example demonstrates a measured performance limitation. A separate query-level experiment is still needed to establish a case where the planner selects a suboptimal strategy for the specific filter being evaluated.

## 12. Testing

The latest recorded automated test command was:

```powershell
python -m pytest -q
```

The run completed with four passing tests:

```text
4 passed in 0.21s
```

## 13. AI-Use Disclosure

AI tools were used for research and to understand concepts related to the problem, including filtered vector search, query planning, and benchmarking. The implementation, testing, and benchmark results were checked against actual project execution.

## 14. Future Improvements

Possible areas for further improvement include:

- Improving selectivity estimation when metadata predicates are correlated.
- Calibrating strategy thresholds against more query distributions.
- Evaluating additional partition-probing policies.
- Testing repeated mutations with different random seeds.
- Comparing latency and recall across a wider range of filter combinations.
- Adding more tests for edge cases and planner decisions.
- Evaluating performance in a clean environment and documenting reproducible benchmark conditions.

These are potential future improvements and should not be interpreted as features already implemented or validated.
