# B07.final experiment report

- Status: **COMPLETE**
- Formal suites: 111/111 complete; 0 blocked; 0 failed
- Quality cases: 5550; query executions across cache states: 11100 (expected 11100)

## Training and dev-selection cost

| Stage | Tokens | USD | CNY | Elapsed ms | Count kind |
|---|---:|---:|---:|---:|---|
| Train action exploration | None | None | None | None | None |
| Dev parameter selection | None | None | None | None | None |

## Suite metrics and usage

| Suite and cache | Queries | Failures | Evidence recall | Answer correctness (0–2) | Tokens | USD | CNY | Latency p50/p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ablation/A01/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A01/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A01/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A01/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A01/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A01/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A02/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| ablation/A02/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| ablation/A02/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| ablation/A02/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| ablation/A02/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| ablation/A02/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| ablation/A03/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A03/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A03/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A03/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A03/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A03/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A04/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A04/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A04/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A04/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A04/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A04/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A05/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A05/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A05/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A05/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A05/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A05/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A06/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A06/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A06/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A06/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A06/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A06/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A07/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A07/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A07/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A07/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A07/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A07/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A08/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A08/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A08/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A08/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A08/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A08/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A09/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A09/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A09/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A09/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A09/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A09/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A10/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A10/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A10/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A10/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| ablation/A10/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| ablation/A10/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/bm25/high/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1028.0/1173.25 |
| system/bm25/high/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 317.0/409.1 |
| system/bm25/high/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1028.0/1173.25 |
| system/bm25/high/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 317.0/409.1 |
| system/bm25/high/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1028.0/1173.25 |
| system/bm25/high/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 317.0/409.1 |
| system/bm25/low/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1028.0/1173.25 |
| system/bm25/low/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 317.0/409.1 |
| system/bm25/low/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1028.0/1173.25 |
| system/bm25/low/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 317.0/409.1 |
| system/bm25/low/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1028.0/1173.25 |
| system/bm25/low/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 317.0/409.1 |
| system/bm25/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1028.0/1173.25 |
| system/bm25/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 317.0/409.1 |
| system/bm25/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1028.0/1173.25 |
| system/bm25/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 317.0/409.1 |
| system/bm25/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1028.0/1173.25 |
| system/bm25/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 317.0/409.1 |
| system/dense/high/seed-17 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1442.5/2380.25 |
| system/dense/high/seed-17 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 6.0/6.0 |
| system/dense/high/seed-29 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1442.5/2380.25 |
| system/dense/high/seed-29 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 6.0/6.0 |
| system/dense/high/seed-43 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1442.5/2380.25 |
| system/dense/high/seed-43 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 6.0/6.0 |
| system/dense/low/seed-17 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1442.5/2380.25 |
| system/dense/low/seed-17 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 6.0/6.0 |
| system/dense/low/seed-29 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1442.5/2380.25 |
| system/dense/low/seed-29 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 6.0/6.0 |
| system/dense/low/seed-43 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1442.5/2380.25 |
| system/dense/low/seed-43 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 6.0/6.0 |
| system/dense/standard/seed-17 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1442.5/2380.25 |
| system/dense/standard/seed-17 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 6.0/6.0 |
| system/dense/standard/seed-29 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1442.5/2380.25 |
| system/dense/standard/seed-29 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 6.0/6.0 |
| system/dense/standard/seed-43 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1442.5/2380.25 |
| system/dense/standard/seed-43 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 6.0/6.0 |
| system/fixed_agent/high/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/fixed_agent/high/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/fixed_agent/high/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/fixed_agent/high/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/fixed_agent/high/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/fixed_agent/high/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/fixed_agent/low/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/fixed_agent/low/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/fixed_agent/low/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/fixed_agent/low/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/fixed_agent/low/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/fixed_agent/low/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/fixed_agent/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/fixed_agent/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/fixed_agent/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/fixed_agent/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/fixed_agent/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/fixed_agent/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/graph/high/seed-17 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1444.5/2382.25 |
| system/graph/high/seed-17 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 8.0/9.0 |
| system/graph/high/seed-29 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1444.5/2382.25 |
| system/graph/high/seed-29 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 8.0/9.0 |
| system/graph/high/seed-43 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1444.5/2382.25 |
| system/graph/high/seed-43 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 8.0/9.0 |
| system/graph/low/seed-17 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1444.5/2382.25 |
| system/graph/low/seed-17 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 8.0/9.0 |
| system/graph/low/seed-29 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1444.5/2382.25 |
| system/graph/low/seed-29 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 8.0/9.0 |
| system/graph/low/seed-43 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1444.5/2382.25 |
| system/graph/low/seed-43 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 8.0/9.0 |
| system/graph/standard/seed-17 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1444.5/2382.25 |
| system/graph/standard/seed-17 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 8.0/9.0 |
| system/graph/standard/seed-29 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1444.5/2382.25 |
| system/graph/standard/seed-29 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 8.0/9.0 |
| system/graph/standard/seed-43 (cold) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 1444.5/2382.25 |
| system/graph/standard/seed-43 (warm) | 50 | 0 | 0.0 | 0.04 | 6431 | 0.001068 | 0.007616 | 8.0/9.0 |
| system/hybrid_rerank/high/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rerank/high/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rerank/high/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rerank/high/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rerank/high/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rerank/high/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rerank/low/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rerank/low/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rerank/low/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rerank/low/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rerank/low/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rerank/low/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rerank/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rerank/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rerank/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rerank/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rerank/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rerank/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rrf/high/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rrf/high/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rrf/high/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rrf/high/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rrf/high/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rrf/high/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rrf/low/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rrf/low/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rrf/low/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rrf/low/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rrf/low/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rrf/low/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rrf/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rrf/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rrf/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rrf/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/hybrid_rrf/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1035.0/1180.25 |
| system/hybrid_rrf/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 324.0/418.09999999999997 |
| system/llm_router/high/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/llm_router/high/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/llm_router/high/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/llm_router/high/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/llm_router/high/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/llm_router/high/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/llm_router/low/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/llm_router/low/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/llm_router/low/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/llm_router/low/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/llm_router/low/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/llm_router/low/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/llm_router/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/llm_router/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/llm_router/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/llm_router/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/llm_router/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/llm_router/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/rule/high/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/rule/high/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/rule/high/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/rule/high/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/rule/high/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/rule/high/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/rule/low/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/rule/low/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/rule/low/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/rule/low/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/rule/low/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/rule/low/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/rule/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/rule/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/rule/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/rule/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/rule/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/rule/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/segr/high/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/segr/high/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/segr/high/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/segr/high/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/segr/high/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/segr/high/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/segr/low/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/segr/low/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/segr/low/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/segr/low/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/segr/low/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/segr/low/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/segr/standard/seed-17 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/segr/standard/seed-17 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/segr/standard/seed-29 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/segr/standard/seed-29 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |
| system/segr/standard/seed-43 (cold) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 1045.0/1183.25 |
| system/segr/standard/seed-43 (warm) | 50 | 0 | 0.5374149659863945 | 0.6 | 5500 | 0.000850 | 0.00600 | 348.5/421.1 |

## Paired source-family comparisons

- bm25 vs hybrid_rerank (evidence_recall): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- bm25 vs hybrid_rerank (answer_correctness): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- bm25 vs hybrid_rerank (quality_score): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- dense vs hybrid_rerank (evidence_recall): difference=-0.5374, 95% CI [-0.9032, -0.3807], clusters=5, confirmatory=True
- dense vs hybrid_rerank (answer_correctness): difference=-0.5600, 95% CI [-0.8537, -0.4167], clusters=5, confirmatory=True
- dense vs hybrid_rerank (quality_score): difference=-0.4116, 95% CI [-0.6696, -0.2972], clusters=5, confirmatory=True
- fixed_agent vs hybrid_rerank (evidence_recall): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- fixed_agent vs hybrid_rerank (answer_correctness): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- fixed_agent vs hybrid_rerank (quality_score): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- graph vs hybrid_rerank (evidence_recall): difference=-0.5374, 95% CI [-0.9032, -0.3807], clusters=5, confirmatory=True
- graph vs hybrid_rerank (answer_correctness): difference=-0.5600, 95% CI [-0.8537, -0.4167], clusters=5, confirmatory=True
- graph vs hybrid_rerank (quality_score): difference=-0.4116, 95% CI [-0.6696, -0.2972], clusters=5, confirmatory=True
- hybrid_rrf vs hybrid_rerank (evidence_recall): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- hybrid_rrf vs hybrid_rerank (answer_correctness): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- hybrid_rrf vs hybrid_rerank (quality_score): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- llm_router vs hybrid_rerank (evidence_recall): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- llm_router vs hybrid_rerank (answer_correctness): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- llm_router vs hybrid_rerank (quality_score): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- rule vs hybrid_rerank (evidence_recall): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- rule vs hybrid_rerank (answer_correctness): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- rule vs hybrid_rerank (quality_score): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- segr vs hybrid_rerank (evidence_recall): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- segr vs hybrid_rerank (answer_correctness): difference=0.0000, 95% CI [0.0000, 0.0000], clusters=5, confirmatory=True
- segr vs hybrid_rerank (quality_score): difference=-0.0000, 95% CI [-0.0000, 0.0000], clusters=5, confirmatory=True

## Standard-budget quality/cost Pareto frontier

- bm25: quality=0.4116, tokens=110, latency_ms=673, USD=0.0051
