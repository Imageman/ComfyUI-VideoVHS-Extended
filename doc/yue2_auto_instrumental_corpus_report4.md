# YuE2 Auto Instrumental corpus report

Corpus: `tmp/raw_abc` (329 UTF-8 files).

| Metric | Result |
|---|---:|
| SBL accepted | 86 |
| New converter accepted | 216 |
| Strict-valid inputs | 89 |
| Inputs requiring repair | 240 |
| Accepted without repairs | 81 |
| Repaired by safe/extended defaults | 135 |
| Rejected for explicit/manual repair | 113 |
| Unexpected converter failures | 0 |
| Ins notes parsed from successful inputs | 31998 |
| Ins notes changed by repair or policy (known count) | 0 |
| Inputs where invalid metadata makes Ins changes unknown | 0 |
| Internal Ins invariant checks passed | 216 |
| Invariant violations among returned results | 0 |
| Vocal notes transferred | 8992 |
| Vocal notes blocked by Ins | 33 |

Structural success rate: 65.7%. Repair success rate: 56.2% (successful repaired files / strict-invalid
inputs). Internal Ins invariant pass rate: 100.0%. This checks converter invariants, not reconstruction
accuracy for damaged source ABC. See the quality report for per-file exact event comparisons and uncertainty.

YuE2 generation and live ComfyUI node loading were not tested in this environment.

## Per-file conversion status

| File | SBL | New | Repairs | SBL s | New s | Delta s |
|---|---|---|---:|---:|---:|---:|
| YuE2_ABC_00001.txt | rejected | ok | 2 |  | 71.811 |  |
| YuE2_ABC_00002.txt | ok | ok | 0 | 76.279 | 76.279 | +0.000 |
| YuE2_ABC_00003.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00004.txt | rejected | ok | 1 |  | 91.429 |  |
| YuE2_ABC_00005.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00006.txt | ok | ok | 0 | 76.000 | 76.000 | +0.000 |
| YuE2_ABC_00007.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00008.txt | rejected | ok | 2 |  | 82.500 |  |
| YuE2_ABC_00009.txt | rejected | ok | 2 |  | 85.872 |  |
| YuE2_ABC_00010.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00011.txt | ok | ok | 0 | 72.000 | 72.000 | +0.000 |
| YuE2_ABC_00012.txt | rejected | ok | 4 |  | 44.651 |  |
| YuE2_ABC_00013.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00014.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00015.txt | ok | ok | 0 | 66.000 | 66.000 | +0.000 |
| YuE2_ABC_00016.txt | ok | ok | 0 | 78.600 | 78.600 | +0.000 |
| YuE2_ABC_00017.txt | rejected | ok | 1 |  | 86.769 |  |
| YuE2_ABC_00018.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00019.txt | rejected | ok | 11 |  | 174.057 |  |
| YuE2_ABC_00020.txt | rejected | ok | 2 |  | 25.714 |  |
| YuE2_ABC_00021.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00022.txt | ok | ok | 0 | 124.724 | 124.724 | +0.000 |
| YuE2_ABC_00023.txt | rejected | ok | 1 |  | 79.592 |  |
| YuE2_ABC_00024.txt | rejected | ok | 3 |  | 59.077 |  |
| YuE2_ABC_00025.txt | ok | ok | 0 | 40.615 | 40.615 | +0.000 |
| YuE2_ABC_00026.txt | rejected | ok | 2 |  | 155.625 |  |
| YuE2_ABC_00027.txt | ok | ok | 0 | 104.308 | 104.308 | +0.000 |
| YuE2_ABC_00028.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00029.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00030.txt | rejected | ok | 2 |  | 156.408 |  |
| YuE2_ABC_00031.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00032.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00033.txt | rejected | ok | 2 |  | 126.750 |  |
| YuE2_ABC_00034.txt | rejected | ok | 3 |  | 98.824 |  |
| YuE2_ABC_00035.txt | rejected | ok | 1 |  | 50.435 |  |
| YuE2_ABC_00036.txt | rejected | ok | 5 |  | 79.266 |  |
| YuE2_ABC_00037.txt | rejected | ok | 3 |  | 113.750 |  |
| YuE2_ABC_00038.txt | ok | ok | 0 | 70.476 | 70.476 | +0.000 |
| YuE2_ABC_00039.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00040.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00041.txt | ok | ok | 0 | 56.364 | 56.364 | +0.000 |
| YuE2_ABC_00042.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00043.txt | ok | ok | 1 | 55.476 | 55.476 | +0.000 |
| YuE2_ABC_00044.txt | rejected | ok | 1 |  | 66.462 |  |
| YuE2_ABC_00045.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00046.txt | rejected | ok | 2 |  | 142.136 |  |
| YuE2_ABC_00047.txt | rejected | ok | 5 |  | 87.273 |  |
| YuE2_ABC_00048.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00049.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00050.txt | ok | ok | 0 | 90.480 | 90.480 | +0.000 |
| YuE2_ABC_00051.txt | ok | ok | 0 | 102.927 | 102.927 | +0.000 |
| YuE2_ABC_00052.txt | rejected | ok | 3 |  | 117.000 |  |
| YuE2_ABC_00053.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00054.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00055.txt | rejected | ok | 1 |  | 107.520 |  |
| YuE2_ABC_00056.txt | ok | ok | 0 | 98.000 | 98.000 | +0.000 |
| YuE2_ABC_00057.txt | rejected | ok | 1 |  | 120.000 |  |
| YuE2_ABC_00058.txt | rejected | ok | 1 |  | 52.121 |  |
| YuE2_ABC_00059.txt | ok | ok | 0 | 172.710 | 172.710 | +0.000 |
| YuE2_ABC_00060.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00061.txt | ok | ok | 2 | 90.909 | 90.909 | +0.000 |
| YuE2_ABC_00062.txt | ok | ok | 0 | 125.825 | 125.825 | +0.000 |
| YuE2_ABC_00063.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00064.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00065.txt | rejected | ok | 2 |  | 85.895 |  |
| YuE2_ABC_00066.txt | ok | ok | 0 | 86.769 | 86.769 | +0.000 |
| YuE2_ABC_00067.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00068.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00069.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00070.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00071.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00072.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00073.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00074.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00075.txt | ok | ok | 0 | 53.538 | 53.538 | +0.000 |
| YuE2_ABC_00076.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00077.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00078.txt | rejected | ok | 2 |  | 34.737 |  |
| YuE2_ABC_00079.txt | rejected | ok | 1 |  | 102.500 |  |
| YuE2_ABC_00080.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00081.txt | ok | ok | 1 | 53.491 | 53.491 | +0.000 |
| YuE2_ABC_00082.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00083.txt | rejected | ok | 5 |  | 117.165 |  |
| YuE2_ABC_00084.txt | ok | ok | 0 | 149.538 | 149.538 | +0.000 |
| YuE2_ABC_00085.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00086.txt | ok | ok | 1 | 44.000 | 44.000 | +0.000 |
| YuE2_ABC_00087.txt | rejected | ok | 1 |  | 42.791 |  |
| YuE2_ABC_00088.txt | rejected | ok | 1 |  | 63.789 |  |
| YuE2_ABC_00089.txt | rejected | ok | 2 |  | 125.538 |  |
| YuE2_ABC_00090.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00091.txt | rejected | ok | 2 |  | 63.750 |  |
| YuE2_ABC_00092.txt | rejected | ok | 1 |  | 100.588 |  |
| YuE2_ABC_00093.txt | rejected | ok | 0 |  | 75.328 |  |
| YuE2_ABC_00094.txt | ok | ok | 0 | 57.231 | 57.231 | +0.000 |
| YuE2_ABC_00095.txt | rejected | ok | 2 |  | 54.240 |  |
| YuE2_ABC_00096.txt | ok | ok | 0 | 102.000 | 102.000 | +0.000 |
| YuE2_ABC_00097.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00098.txt | rejected | ok | 0 |  | 149.760 |  |
| YuE2_ABC_00099.txt | rejected | ok | 1 |  | 110.769 |  |
| YuE2_ABC_00100.txt | rejected | ok | 3 |  | 97.250 |  |
| YuE2_ABC_00101.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00102.txt | rejected | ok | 1 |  | 121.739 |  |
| YuE2_ABC_00103.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00104.txt | ok | ok | 0 | 110.769 | 110.769 | +0.000 |
| YuE2_ABC_00105.txt | rejected | ok | 2 |  | 92.308 |  |
| YuE2_ABC_00106.txt | rejected | ok | 4 |  | 157.385 |  |
| YuE2_ABC_00107.txt | ok | ok | 0 | 56.887 | 56.887 | +0.000 |
| YuE2_ABC_00108.txt | rejected | ok | 1 |  | 198.000 |  |
| YuE2_ABC_00109.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00110.txt | ok | ok | 0 | 106.000 | 106.000 | +0.000 |
| YuE2_ABC_00111.txt | ok | ok | 0 | 88.073 | 88.073 | +0.000 |
| YuE2_ABC_00112.txt | ok | ok | 0 | 118.899 | 118.899 | +0.000 |
| YuE2_ABC_00113.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00114.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00115.txt | ok | ok | 0 | 66.000 | 66.000 | +0.000 |
| YuE2_ABC_00116.txt | rejected | ok | 2 |  | 182.609 |  |
| YuE2_ABC_00117.txt | ok | ok | 0 | 76.923 | 76.923 | +0.000 |
| YuE2_ABC_00118.txt | ok | ok | 0 | 82.000 | 82.000 | +0.000 |
| YuE2_ABC_00119.txt | rejected | ok | 6 |  | 122.538 |  |
| YuE2_ABC_00120.txt | rejected | ok | 1 |  | 77.538 |  |
| YuE2_ABC_00121.txt | rejected | ok | 4 |  | 88.302 |  |
| YuE2_ABC_00122.txt | rejected | ok | 2 |  | 69.333 |  |
| YuE2_ABC_00123.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00124.txt | ok | ok | 0 | 124.000 | 124.000 | +0.000 |
| YuE2_ABC_00125.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00126.txt | ok | ok | 1 | 93.793 | 93.793 | +0.000 |
| YuE2_ABC_00127.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00128.txt | rejected | ok | 4 |  | 118.879 |  |
| YuE2_ABC_00129.txt | rejected | ok | 4 |  | 40.421 |  |
| YuE2_ABC_00130.txt | ok | ok | 0 | 106.500 | 106.500 | +0.000 |
| YuE2_ABC_00131.txt | ok | ok | 0 | 75.833 | 75.833 | +0.000 |
| YuE2_ABC_00132.txt | rejected | ok | 1 |  | 111.176 |  |
| YuE2_ABC_00133.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00134.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00135.txt | rejected | ok | 1 |  | 78.000 |  |
| YuE2_ABC_00136.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00137.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00138.txt | ok | ok | 0 | 60.923 | 60.923 | +0.000 |
| YuE2_ABC_00139.txt | rejected | ok | 1 |  | 97.846 |  |
| YuE2_ABC_00140.txt | ok | ok | 1 | 51.024 | 51.024 | +0.000 |
| YuE2_ABC_00141.txt | rejected | ok | 1 |  | 96.000 |  |
| YuE2_ABC_00142.txt | rejected | ok | 7 |  | 78.316 |  |
| YuE2_ABC_00143.txt | rejected | ok | 2 |  | 79.149 |  |
| YuE2_ABC_00144.txt | rejected | ok | 2 |  | 84.820 |  |
| YuE2_ABC_00145.txt | rejected | ok | 1 |  | 54.000 |  |
| YuE2_ABC_00146.txt | ok | ok | 0 | 62.222 | 62.222 | +0.000 |
| YuE2_ABC_00147.txt | ok | ok | 0 | 63.692 | 63.692 | +0.000 |
| YuE2_ABC_00148.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00149.txt | rejected | ok | 1 |  | 131.250 |  |
| YuE2_ABC_00150.txt | rejected | ok | 1 |  | 188.411 |  |
| YuE2_ABC_00151.txt | rejected | ok | 1 |  | 66.000 |  |
| YuE2_ABC_00152.txt | ok | ok | 0 | 57.250 | 57.250 | +0.000 |
| YuE2_ABC_00153.txt | rejected | ok | 5 |  | 74.118 |  |
| YuE2_ABC_00154.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00155.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00156.txt | rejected | ok | 1 |  | 100.250 |  |
| YuE2_ABC_00157.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00158.txt | rejected | ok | 1 |  | 112.250 |  |
| YuE2_ABC_00159.txt | rejected | ok | 3 |  | 110.538 |  |
| YuE2_ABC_00160.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00161.txt | ok | ok | 0 | 40.976 | 40.976 | +0.000 |
| YuE2_ABC_00162.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00163.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00164.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00165.txt | ok | ok | 0 | 80.000 | 80.000 | +0.000 |
| YuE2_ABC_00166.txt | rejected | ok | 1 |  | 58.667 |  |
| YuE2_ABC_00167.txt | ok | ok | 0 | 84.000 | 84.000 | +0.000 |
| YuE2_ABC_00168.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00169.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00170.txt | ok | ok | 0 | 110.769 | 110.769 | +0.000 |
| YuE2_ABC_00171.txt | rejected | ok | 1 |  | 139.200 |  |
| YuE2_ABC_00172.txt | rejected | ok | 4 |  | 62.474 |  |
| YuE2_ABC_00173.txt | rejected | ok | 4 |  | 175.385 |  |
| YuE2_ABC_00174.txt | rejected | ok | 2 |  | 109.412 |  |
| YuE2_ABC_00175.txt | rejected | ok | 4 |  | 60.000 |  |
| YuE2_ABC_00176.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00177.txt | ok | ok | 0 | 142.222 | 142.222 | +0.000 |
| YuE2_ABC_00178.txt | ok | ok | 0 | 121.875 | 121.875 | +0.000 |
| YuE2_ABC_00179.txt | rejected | ok | 2 |  | 238.879 |  |
| YuE2_ABC_00180.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00181.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00182.txt | ok | ok | 0 | 81.500 | 81.500 | +0.000 |
| YuE2_ABC_00183.txt | rejected | ok | 3 |  | 122.250 |  |
| YuE2_ABC_00184.txt | ok | ok | 0 | 123.636 | 123.636 | +0.000 |
| YuE2_ABC_00185.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00186.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00187.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00188.txt | rejected | ok | 6 |  | 35.917 |  |
| YuE2_ABC_00189.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00190.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00191.txt | ok | ok | 0 | 130.000 | 130.000 | +0.000 |
| YuE2_ABC_00192.txt | rejected | ok | 2 |  | 88.819 |  |
| YuE2_ABC_00193.txt | ok | ok | 0 | 78.000 | 78.000 | +0.000 |
| YuE2_ABC_00194.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00195.txt | rejected | ok | 5 |  | 104.000 |  |
| YuE2_ABC_00196.txt | rejected | ok | 2 |  | 82.222 |  |
| YuE2_ABC_00197.txt | ok | ok | 0 | 72.453 | 72.453 | +0.000 |
| YuE2_ABC_00198.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00199.txt | rejected | ok | 3 |  | 56.000 |  |
| YuE2_ABC_00200.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00201.txt | ok | ok | 0 | 96.000 | 96.000 | +0.000 |
| YuE2_ABC_00202.txt | ok | ok | 0 | 101.538 | 101.538 | +0.000 |
| YuE2_ABC_00203.txt | rejected | ok | 1 |  | 123.923 |  |
| YuE2_ABC_00204.txt | ok | ok | 0 | 77.299 | 77.299 | +0.000 |
| YuE2_ABC_00205.txt | rejected | ok | 5 |  | 115.833 |  |
| YuE2_ABC_00206.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00207.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00208.txt | rejected | ok | 1 |  | 23.000 |  |
| YuE2_ABC_00209.txt | ok | ok | 0 | 107.077 | 107.077 | +0.000 |
| YuE2_ABC_00210.txt | rejected | ok | 4 |  | 119.500 |  |
| YuE2_ABC_00211.txt | ok | ok | 0 | 49.846 | 49.846 | +0.000 |
| YuE2_ABC_00212.txt | rejected | ok | 1 |  | 90.000 |  |
| YuE2_ABC_00213.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00214.txt | rejected | ok | 5 |  | 67.556 |  |
| YuE2_ABC_00215.txt | ok | ok | 0 | 104.000 | 104.000 | +0.000 |
| YuE2_ABC_00216.txt | rejected | ok | 2 |  | 55.000 |  |
| YuE2_ABC_00217.txt | rejected | ok | 2 |  | 53.750 |  |
| YuE2_ABC_00218.txt | rejected | ok | 2 |  | 31.111 |  |
| YuE2_ABC_00219.txt | ok | ok | 0 | 68.000 | 68.000 | +0.000 |
| YuE2_ABC_00220.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00221.txt | rejected | ok | 4 |  | 80.000 |  |
| YuE2_ABC_00222.txt | rejected | ok | 3 |  | 104.160 |  |
| YuE2_ABC_00223.txt | rejected | ok | 2 |  | 112.000 |  |
| YuE2_ABC_00224.txt | rejected | ok | 4 |  | 96.000 |  |
| YuE2_ABC_00225.txt | rejected | ok | 5 |  | 112.381 |  |
| YuE2_ABC_00226.txt | ok | ok | 0 | 105.882 | 105.882 | +0.000 |
| YuE2_ABC_00227.txt | ok | ok | 0 | 214.000 | 214.000 | +0.000 |
| YuE2_ABC_00228.txt | rejected | ok | 2 |  | 122.830 |  |
| YuE2_ABC_00229.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00230.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00231.txt | rejected | ok | 1 |  | 117.778 |  |
| YuE2_ABC_00232.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00233.txt | rejected | ok | 4 |  | 125.780 |  |
| YuE2_ABC_00234.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00235.txt | rejected | ok | 3 |  | 99.900 |  |
| YuE2_ABC_00236.txt | ok | ok | 1 | 94.000 | 94.000 | +0.000 |
| YuE2_ABC_00237.txt | ok | ok | 0 | 46.154 | 46.154 | +0.000 |
| YuE2_ABC_00238.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00239.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00240.txt | rejected | ok | 1 |  | 92.477 |  |
| YuE2_ABC_00241.txt | ok | ok | 0 | 99.692 | 99.692 | +0.000 |
| YuE2_ABC_00242.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00243.txt | ok | ok | 0 | 75.692 | 75.692 | +0.000 |
| YuE2_ABC_00244.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00245.txt | rejected | ok | 1 |  | 92.308 |  |
| YuE2_ABC_00246.txt | ok | ok | 0 | 107.077 | 107.077 | +0.000 |
| YuE2_ABC_00247.txt | rejected | ok | 3 |  | 151.376 |  |
| YuE2_ABC_00248.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00249.txt | ok | ok | 0 | 110.000 | 110.000 | +0.000 |
| YuE2_ABC_00250.txt | rejected | ok | 1 |  | 123.692 |  |
| YuE2_ABC_00251.txt | ok | ok | 0 | 108.000 | 108.000 | +0.000 |
| YuE2_ABC_00252.txt | rejected | ok | 6 |  | 115.400 |  |
| YuE2_ABC_00253.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00254.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00255.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00256.txt | ok | ok | 0 | 84.375 | 84.375 | +0.000 |
| YuE2_ABC_00257.txt | rejected | ok | 2 |  | 88.846 |  |
| YuE2_ABC_00258.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00259.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00260.txt | rejected | ok | 4 |  | 80.000 |  |
| YuE2_ABC_00261.txt | rejected | ok | 2 |  | 101.339 |  |
| YuE2_ABC_00262.txt | rejected | ok | 5 |  | 195.923 |  |
| YuE2_ABC_00263.txt | ok | ok | 0 | 94.154 | 94.154 | +0.000 |
| YuE2_ABC_00264.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00265.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00266.txt | rejected | ok | 2 |  | 83.077 |  |
| YuE2_ABC_00267.txt | ok | ok | 0 | 135.738 | 135.738 | +0.000 |
| YuE2_ABC_00268.txt | rejected | ok | 1 |  | 83.077 |  |
| YuE2_ABC_00269.txt | ok | ok | 0 | 79.385 | 79.385 | +0.000 |
| YuE2_ABC_00270.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00271.txt | rejected | ok | 4 |  | 60.250 |  |
| YuE2_ABC_00272.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00273.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00274.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00275.txt | ok | ok | 0 | 97.846 | 97.846 | +0.000 |
| YuE2_ABC_00276.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00277.txt | ok | ok | 0 | 58.824 | 58.824 | +0.000 |
| YuE2_ABC_00278.txt | rejected | ok | 2 |  | 73.600 |  |
| YuE2_ABC_00279.txt | ok | ok | 0 | 83.308 | 83.308 | +0.000 |
| YuE2_ABC_00280.txt | rejected | ok | 1 |  | 134.640 |  |
| YuE2_ABC_00281.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00282.txt | ok | ok | 0 | 123.711 | 123.711 | +0.000 |
| YuE2_ABC_00283.txt | rejected | ok | 5 |  | 91.769 |  |
| YuE2_ABC_00284.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00285.txt | rejected | ok | 4 |  | 94.250 |  |
| YuE2_ABC_00286.txt | rejected | ok | 2 |  | 120.000 |  |
| YuE2_ABC_00287.txt | rejected | ok | 6 |  | 90.250 |  |
| YuE2_ABC_00288.txt | rejected | ok | 3 |  | 84.673 |  |
| YuE2_ABC_00289.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00290.txt | rejected | ok | 2 |  | 74.667 |  |
| YuE2_ABC_00291.txt | rejected | ok | 1 |  | 77.769 |  |
| YuE2_ABC_00292.txt | rejected | ok | 2 |  | 72.250 |  |
| YuE2_ABC_00293.txt | rejected | ok | 1 |  | 58.776 |  |
| YuE2_ABC_00294.txt | ok | ok | 0 | 76.364 | 76.364 | +0.000 |
| YuE2_ABC_00295.txt | rejected | ok | 2 |  | 83.556 |  |
| YuE2_ABC_00296.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00297.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00298.txt | ok | ok | 0 | 51.692 | 51.692 | +0.000 |
| YuE2_ABC_00299.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00300.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00301.txt | rejected | ok | 2 |  | 70.857 |  |
| YuE2_ABC_00302.txt | ok | ok | 0 | 48.000 | 48.000 | +0.000 |
| YuE2_ABC_00303.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00304.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00305.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00306.txt | ok | ok | 0 | 94.000 | 94.000 | +0.000 |
| YuE2_ABC_00307.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00308.txt | rejected | ok | 1 |  | 134.173 |  |
| YuE2_ABC_00309.txt | ok | ok | 0 | 60.000 | 60.000 | +0.000 |
| YuE2_ABC_00310.txt | rejected | ok | 2 |  | 79.615 |  |
| YuE2_ABC_00311.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00312.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00313.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00314.txt | rejected | ok | 1 |  | 92.308 |  |
| YuE2_ABC_00315.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00316.txt | rejected | ok | 1 |  | 77.769 |  |
| YuE2_ABC_00317.txt | rejected | ok | 1 |  | 92.444 |  |
| YuE2_ABC_00318.txt | ok | ok | 0 | 94.154 | 94.154 | +0.000 |
| YuE2_ABC_00319.txt | rejected | ok | 3 |  | 63.600 |  |
| YuE2_ABC_00320.txt | rejected | ok | 2 |  | 39.184 |  |
| YuE2_ABC_00321.txt | rejected | ok | 1 |  | 71.811 |  |
| YuE2_ABC_00322.txt | rejected | ok | 2 |  | 114.894 |  |
| YuE2_ABC_00323.txt | ok | ok | 0 | 85.333 | 85.333 | +0.000 |
| YuE2_ABC_00324.txt | rejected | ok | 1 |  | 54.375 |  |
| YuE2_ABC_00325.txt | ok | ok | 0 | 79.500 | 79.500 | +0.000 |
| YuE2_ABC_00326.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00327.txt | rejected | rejected | 0 |  |  |  |
| YuE2_ABC_00328.txt | ok | ok | 0 | 79.579 | 79.579 | +0.000 |
| YuE2_ABC_00329.txt | ok | ok | 0 | 73.846 | 73.846 | +0.000 |

## Per-file music statistics

| File | Ins notes | Ins changed | Vocal moved | Vocal not moved | Ins checked |
|---|---:|---:|---:|---:|---|
| YuE2_ABC_00001.txt | 62 | 0 | 24 | 0 | true |
| YuE2_ABC_00002.txt | 91 | 0 | 29 | 0 | true |
| YuE2_ABC_00003.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00004.txt | 97 | 0 | 33 | 0 | true |
| YuE2_ABC_00005.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00006.txt | 116 | 0 | 8 | 0 | true |
| YuE2_ABC_00007.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00008.txt | 137 | 0 | 0 | 0 | true |
| YuE2_ABC_00009.txt | 68 | 0 | 32 | 0 | true |
| YuE2_ABC_00010.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00011.txt | 75 | 0 | 15 | 0 | true |
| YuE2_ABC_00012.txt | 42 | 0 | 26 | 0 | true |
| YuE2_ABC_00013.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00014.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00015.txt | 54 | 0 | 36 | 0 | true |
| YuE2_ABC_00016.txt | 80 | 0 | 32 | 0 | true |
| YuE2_ABC_00017.txt | 133 | 0 | 34 | 0 | true |
| YuE2_ABC_00018.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00019.txt | 152 | 0 | 34 | 1 | true |
| YuE2_ABC_00020.txt | 43 | 0 | 13 | 0 | true |
| YuE2_ABC_00021.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00022.txt | 392 | 0 | 117 | 0 | true |
| YuE2_ABC_00023.txt | 206 | 0 | 0 | 0 | true |
| YuE2_ABC_00024.txt | 172 | 0 | 206 | 0 | true |
| YuE2_ABC_00025.txt | 41 | 0 | 15 | 0 | true |
| YuE2_ABC_00026.txt | 204 | 0 | 26 | 0 | true |
| YuE2_ABC_00027.txt | 412 | 0 | 65 | 0 | true |
| YuE2_ABC_00028.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00029.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00030.txt | 128 | 0 | 56 | 0 | true |
| YuE2_ABC_00031.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00032.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00033.txt | 214 | 0 | 0 | 0 | true |
| YuE2_ABC_00034.txt | 120 | 0 | 111 | 0 | true |
| YuE2_ABC_00035.txt | 40 | 0 | 30 | 0 | true |
| YuE2_ABC_00036.txt | 69 | 0 | 57 | 0 | true |
| YuE2_ABC_00037.txt | 146 | 0 | 28 | 6 | true |
| YuE2_ABC_00038.txt | 113 | 0 | 0 | 0 | true |
| YuE2_ABC_00039.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00040.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00041.txt | 87 | 0 | 7 | 0 | true |
| YuE2_ABC_00042.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00043.txt | 66 | 0 | 6 | 0 | true |
| YuE2_ABC_00044.txt | 63 | 0 | 37 | 0 | true |
| YuE2_ABC_00045.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00046.txt | 95 | 0 | 44 | 0 | true |
| YuE2_ABC_00047.txt | 300 | 0 | 179 | 4 | true |
| YuE2_ABC_00048.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00049.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00050.txt | 77 | 0 | 25 | 0 | true |
| YuE2_ABC_00051.txt | 479 | 0 | 46 | 0 | true |
| YuE2_ABC_00052.txt | 295 | 0 | 242 | 0 | true |
| YuE2_ABC_00053.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00054.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00055.txt | 83 | 0 | 23 | 0 | true |
| YuE2_ABC_00056.txt | 171 | 0 | 0 | 0 | true |
| YuE2_ABC_00057.txt | 65 | 0 | 69 | 0 | true |
| YuE2_ABC_00058.txt | 144 | 0 | 28 | 4 | true |
| YuE2_ABC_00059.txt | 174 | 0 | 49 | 0 | true |
| YuE2_ABC_00060.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00061.txt | 461 | 0 | 216 | 0 | true |
| YuE2_ABC_00062.txt | 172 | 0 | 385 | 0 | true |
| YuE2_ABC_00063.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00064.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00065.txt | 126 | 0 | 0 | 0 | true |
| YuE2_ABC_00066.txt | 121 | 0 | 11 | 0 | true |
| YuE2_ABC_00067.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00068.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00069.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00070.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00071.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00072.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00073.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00074.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00075.txt | 126 | 0 | 110 | 0 | true |
| YuE2_ABC_00076.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00077.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00078.txt | 23 | 0 | 14 | 0 | true |
| YuE2_ABC_00079.txt | 68 | 0 | 58 | 0 | true |
| YuE2_ABC_00080.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00081.txt | 68 | 0 | 13 | 0 | true |
| YuE2_ABC_00082.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00083.txt | 119 | 0 | 26 | 0 | true |
| YuE2_ABC_00084.txt | 427 | 0 | 222 | 0 | true |
| YuE2_ABC_00085.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00086.txt | 157 | 0 | 5 | 0 | true |
| YuE2_ABC_00087.txt | 123 | 0 | 0 | 0 | true |
| YuE2_ABC_00088.txt | 46 | 0 | 27 | 0 | true |
| YuE2_ABC_00089.txt | 374 | 0 | 308 | 0 | true |
| YuE2_ABC_00090.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00091.txt | 81 | 0 | 3 | 0 | true |
| YuE2_ABC_00092.txt | 122 | 0 | 7 | 0 | true |
| YuE2_ABC_00093.txt | 145 | 0 | 0 | 0 | true |
| YuE2_ABC_00094.txt | 27 | 0 | 31 | 0 | true |
| YuE2_ABC_00095.txt | 162 | 0 | 147 | 14 | true |
| YuE2_ABC_00096.txt | 238 | 0 | 95 | 0 | true |
| YuE2_ABC_00097.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00098.txt | 227 | 0 | 8 | 0 | true |
| YuE2_ABC_00099.txt | 107 | 0 | 55 | 0 | true |
| YuE2_ABC_00100.txt | 161 | 0 | 1 | 0 | true |
| YuE2_ABC_00101.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00102.txt | 199 | 0 | 37 | 0 | true |
| YuE2_ABC_00103.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00104.txt | 362 | 0 | 135 | 0 | true |
| YuE2_ABC_00105.txt | 144 | 0 | 17 | 0 | true |
| YuE2_ABC_00106.txt | 242 | 0 | 7 | 0 | true |
| YuE2_ABC_00107.txt | 50 | 0 | 28 | 0 | true |
| YuE2_ABC_00108.txt | 167 | 0 | 108 | 1 | true |
| YuE2_ABC_00109.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00110.txt | 213 | 0 | 185 | 0 | true |
| YuE2_ABC_00111.txt | 71 | 0 | 36 | 0 | true |
| YuE2_ABC_00112.txt | 124 | 0 | 41 | 0 | true |
| YuE2_ABC_00113.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00114.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00115.txt | 148 | 0 | 98 | 0 | true |
| YuE2_ABC_00116.txt | 343 | 0 | 0 | 0 | true |
| YuE2_ABC_00117.txt | 108 | 0 | 35 | 0 | true |
| YuE2_ABC_00118.txt | 115 | 0 | 12 | 0 | true |
| YuE2_ABC_00119.txt | 107 | 0 | 79 | 0 | true |
| YuE2_ABC_00120.txt | 129 | 0 | 0 | 0 | true |
| YuE2_ABC_00121.txt | 100 | 0 | 25 | 0 | true |
| YuE2_ABC_00122.txt | 65 | 0 | 27 | 0 | true |
| YuE2_ABC_00123.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00124.txt | 104 | 0 | 42 | 0 | true |
| YuE2_ABC_00125.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00126.txt | 203 | 0 | 119 | 0 | true |
| YuE2_ABC_00127.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00128.txt | 84 | 0 | 32 | 0 | true |
| YuE2_ABC_00129.txt | 87 | 0 | 1 | 0 | true |
| YuE2_ABC_00130.txt | 89 | 0 | 28 | 0 | true |
| YuE2_ABC_00131.txt | 88 | 0 | 21 | 0 | true |
| YuE2_ABC_00132.txt | 204 | 0 | 5 | 0 | true |
| YuE2_ABC_00133.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00134.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00135.txt | 103 | 0 | 20 | 0 | true |
| YuE2_ABC_00136.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00137.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00138.txt | 79 | 0 | 49 | 0 | true |
| YuE2_ABC_00139.txt | 94 | 0 | 47 | 0 | true |
| YuE2_ABC_00140.txt | 130 | 0 | 7 | 0 | true |
| YuE2_ABC_00141.txt | 83 | 0 | 55 | 0 | true |
| YuE2_ABC_00142.txt | 165 | 0 | 3 | 0 | true |
| YuE2_ABC_00143.txt | 71 | 0 | 44 | 0 | true |
| YuE2_ABC_00144.txt | 99 | 0 | 13 | 0 | true |
| YuE2_ABC_00145.txt | 57 | 0 | 35 | 0 | true |
| YuE2_ABC_00146.txt | 72 | 0 | 9 | 0 | true |
| YuE2_ABC_00147.txt | 93 | 0 | 49 | 1 | true |
| YuE2_ABC_00148.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00149.txt | 113 | 0 | 46 | 0 | true |
| YuE2_ABC_00150.txt | 278 | 0 | 0 | 0 | true |
| YuE2_ABC_00151.txt | 233 | 0 | 63 | 0 | true |
| YuE2_ABC_00152.txt | 75 | 0 | 24 | 0 | true |
| YuE2_ABC_00153.txt | 61 | 0 | 11 | 0 | true |
| YuE2_ABC_00154.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00155.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00156.txt | 128 | 0 | 27 | 0 | true |
| YuE2_ABC_00157.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00158.txt | 154 | 0 | 16 | 0 | true |
| YuE2_ABC_00159.txt | 105 | 0 | 33 | 0 | true |
| YuE2_ABC_00160.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00161.txt | 138 | 0 | 39 | 0 | true |
| YuE2_ABC_00162.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00163.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00164.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00165.txt | 115 | 0 | 18 | 0 | true |
| YuE2_ABC_00166.txt | 225 | 0 | 22 | 0 | true |
| YuE2_ABC_00167.txt | 287 | 0 | 70 | 0 | true |
| YuE2_ABC_00168.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00169.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00170.txt | 141 | 0 | 18 | 0 | true |
| YuE2_ABC_00171.txt | 321 | 0 | 0 | 0 | true |
| YuE2_ABC_00172.txt | 95 | 0 | 24 | 0 | true |
| YuE2_ABC_00173.txt | 275 | 0 | 28 | 0 | true |
| YuE2_ABC_00174.txt | 176 | 0 | 3 | 0 | true |
| YuE2_ABC_00175.txt | 78 | 0 | 17 | 0 | true |
| YuE2_ABC_00176.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00177.txt | 254 | 0 | 1 | 0 | true |
| YuE2_ABC_00178.txt | 268 | 0 | 116 | 0 | true |
| YuE2_ABC_00179.txt | 938 | 0 | 61 | 0 | true |
| YuE2_ABC_00180.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00181.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00182.txt | 390 | 0 | 2 | 0 | true |
| YuE2_ABC_00183.txt | 95 | 0 | 67 | 0 | true |
| YuE2_ABC_00184.txt | 120 | 0 | 40 | 0 | true |
| YuE2_ABC_00185.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00186.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00187.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00188.txt | 31 | 0 | 30 | 0 | true |
| YuE2_ABC_00189.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00190.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00191.txt | 192 | 0 | 5 | 0 | true |
| YuE2_ABC_00192.txt | 242 | 0 | 46 | 0 | true |
| YuE2_ABC_00193.txt | 319 | 0 | 84 | 0 | true |
| YuE2_ABC_00194.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00195.txt | 83 | 0 | 42 | 0 | true |
| YuE2_ABC_00196.txt | 98 | 0 | 16 | 0 | true |
| YuE2_ABC_00197.txt | 61 | 0 | 25 | 0 | true |
| YuE2_ABC_00198.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00199.txt | 31 | 0 | 29 | 0 | true |
| YuE2_ABC_00200.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00201.txt | 119 | 0 | 10 | 0 | true |
| YuE2_ABC_00202.txt | 123 | 0 | 31 | 0 | true |
| YuE2_ABC_00203.txt | 180 | 0 | 61 | 0 | true |
| YuE2_ABC_00204.txt | 105 | 0 | 19 | 0 | true |
| YuE2_ABC_00205.txt | 66 | 0 | 10 | 0 | true |
| YuE2_ABC_00206.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00207.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00208.txt | 58 | 0 | 2 | 0 | true |
| YuE2_ABC_00209.txt | 84 | 0 | 59 | 0 | true |
| YuE2_ABC_00210.txt | 118 | 0 | 11 | 0 | true |
| YuE2_ABC_00211.txt | 125 | 0 | 108 | 0 | true |
| YuE2_ABC_00212.txt | 138 | 0 | 23 | 0 | true |
| YuE2_ABC_00213.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00214.txt | 74 | 0 | 17 | 0 | true |
| YuE2_ABC_00215.txt | 141 | 0 | 18 | 0 | true |
| YuE2_ABC_00216.txt | 71 | 0 | 40 | 0 | true |
| YuE2_ABC_00217.txt | 37 | 0 | 32 | 0 | true |
| YuE2_ABC_00218.txt | 50 | 0 | 3 | 0 | true |
| YuE2_ABC_00219.txt | 56 | 0 | 29 | 0 | true |
| YuE2_ABC_00220.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00221.txt | 91 | 0 | 18 | 0 | true |
| YuE2_ABC_00222.txt | 452 | 0 | 23 | 0 | true |
| YuE2_ABC_00223.txt | 113 | 0 | 32 | 0 | true |
| YuE2_ABC_00224.txt | 133 | 0 | 18 | 0 | true |
| YuE2_ABC_00225.txt | 95 | 0 | 28 | 1 | true |
| YuE2_ABC_00226.txt | 107 | 0 | 24 | 0 | true |
| YuE2_ABC_00227.txt | 255 | 0 | 136 | 0 | true |
| YuE2_ABC_00228.txt | 106 | 0 | 28 | 0 | true |
| YuE2_ABC_00229.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00230.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00231.txt | 111 | 0 | 23 | 0 | true |
| YuE2_ABC_00232.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00233.txt | 114 | 0 | 16 | 0 | true |
| YuE2_ABC_00234.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00235.txt | 150 | 0 | 51 | 0 | true |
| YuE2_ABC_00236.txt | 253 | 0 | 203 | 0 | true |
| YuE2_ABC_00237.txt | 53 | 0 | 9 | 0 | true |
| YuE2_ABC_00238.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00239.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00240.txt | 78 | 0 | 17 | 0 | true |
| YuE2_ABC_00241.txt | 295 | 0 | 58 | 0 | true |
| YuE2_ABC_00242.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00243.txt | 86 | 0 | 28 | 0 | true |
| YuE2_ABC_00244.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00245.txt | 87 | 0 | 36 | 0 | true |
| YuE2_ABC_00246.txt | 490 | 0 | 47 | 0 | true |
| YuE2_ABC_00247.txt | 199 | 0 | 6 | 0 | true |
| YuE2_ABC_00248.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00249.txt | 117 | 0 | 55 | 0 | true |
| YuE2_ABC_00250.txt | 204 | 0 | 15 | 0 | true |
| YuE2_ABC_00251.txt | 168 | 0 | 3 | 0 | true |
| YuE2_ABC_00252.txt | 48 | 0 | 45 | 1 | true |
| YuE2_ABC_00253.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00254.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00255.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00256.txt | 326 | 0 | 60 | 0 | true |
| YuE2_ABC_00257.txt | 109 | 0 | 16 | 0 | true |
| YuE2_ABC_00258.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00259.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00260.txt | 80 | 0 | 28 | 0 | true |
| YuE2_ABC_00261.txt | 160 | 0 | 13 | 0 | true |
| YuE2_ABC_00262.txt | 360 | 0 | 0 | 0 | true |
| YuE2_ABC_00263.txt | 310 | 0 | 31 | 0 | true |
| YuE2_ABC_00264.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00265.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00266.txt | 69 | 0 | 260 | 0 | true |
| YuE2_ABC_00267.txt | 170 | 0 | 12 | 0 | true |
| YuE2_ABC_00268.txt | 74 | 0 | 21 | 0 | true |
| YuE2_ABC_00269.txt | 191 | 0 | 10 | 0 | true |
| YuE2_ABC_00270.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00271.txt | 78 | 0 | 15 | 0 | true |
| YuE2_ABC_00272.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00273.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00274.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00275.txt | 130 | 0 | 20 | 0 | true |
| YuE2_ABC_00276.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00277.txt | 53 | 0 | 166 | 0 | true |
| YuE2_ABC_00278.txt | 68 | 0 | 25 | 0 | true |
| YuE2_ABC_00279.txt | 70 | 0 | 30 | 0 | true |
| YuE2_ABC_00280.txt | 118 | 0 | 55 | 0 | true |
| YuE2_ABC_00281.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00282.txt | 151 | 0 | 11 | 0 | true |
| YuE2_ABC_00283.txt | 111 | 0 | 9 | 0 | true |
| YuE2_ABC_00284.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00285.txt | 138 | 0 | 0 | 0 | true |
| YuE2_ABC_00286.txt | 82 | 0 | 88 | 0 | true |
| YuE2_ABC_00287.txt | 136 | 0 | 14 | 0 | true |
| YuE2_ABC_00288.txt | 75 | 0 | 18 | 0 | true |
| YuE2_ABC_00289.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00290.txt | 109 | 0 | 9 | 0 | true |
| YuE2_ABC_00291.txt | 132 | 0 | 11 | 0 | true |
| YuE2_ABC_00292.txt | 58 | 0 | 15 | 0 | true |
| YuE2_ABC_00293.txt | 59 | 0 | 12 | 0 | true |
| YuE2_ABC_00294.txt | 526 | 0 | 0 | 0 | true |
| YuE2_ABC_00295.txt | 87 | 0 | 15 | 0 | true |
| YuE2_ABC_00296.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00297.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00298.txt | 255 | 0 | 13 | 0 | true |
| YuE2_ABC_00299.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00300.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00301.txt | 81 | 0 | 13 | 0 | true |
| YuE2_ABC_00302.txt | 54 | 0 | 25 | 0 | true |
| YuE2_ABC_00303.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00304.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00305.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00306.txt | 103 | 0 | 23 | 0 | true |
| YuE2_ABC_00307.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00308.txt | 219 | 0 | 47 | 0 | true |
| YuE2_ABC_00309.txt | 63 | 0 | 20 | 0 | true |
| YuE2_ABC_00310.txt | 133 | 0 | 20 | 0 | true |
| YuE2_ABC_00311.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00312.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00313.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00314.txt | 99 | 0 | 35 | 0 | true |
| YuE2_ABC_00315.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00316.txt | 140 | 0 | 0 | 0 | true |
| YuE2_ABC_00317.txt | 96 | 0 | 41 | 0 | true |
| YuE2_ABC_00318.txt | 73 | 0 | 35 | 0 | true |
| YuE2_ABC_00319.txt | 75 | 0 | 22 | 0 | true |
| YuE2_ABC_00320.txt | 39 | 0 | 26 | 0 | true |
| YuE2_ABC_00321.txt | 62 | 0 | 37 | 0 | true |
| YuE2_ABC_00322.txt | 119 | 0 | 52 | 0 | true |
| YuE2_ABC_00323.txt | 270 | 0 | 46 | 0 | true |
| YuE2_ABC_00324.txt | 84 | 0 | 3 | 0 | true |
| YuE2_ABC_00325.txt | 293 | 0 | 91 | 0 | true |
| YuE2_ABC_00326.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00327.txt | 0 | 0 | 0 | 0 | n/a |
| YuE2_ABC_00328.txt | 199 | 0 | 55 | 0 | true |
| YuE2_ABC_00329.txt | 262 | 0 | 36 | 0 | true |

## Unrecoverable ABC inputs

- `YuE2_ABC_00003.txt`: Measure 41 in vocal is short by 1/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00005.txt`: Measure 14 in vocal is short by 1/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00007.txt`: Unsupported ABC notation at line 51, near ',4'.
- `YuE2_ABC_00010.txt`: Unsupported ABC notation at line 69, near 'sus4'.
- `YuE2_ABC_00013.txt`: Unsupported ABC notation at line 40, near 'im7'.
- `YuE2_ABC_00014.txt`: Unsupported ABC notation at line 32, near "'16-_".
- `YuE2_ABC_00018.txt`: Measure 15 in vocal is short by 1/16 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00021.txt`: Measure 19 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00028.txt`: Measure 46 in vocal is short by 1/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00029.txt`: Aligned short measures in Vocal and Ins suggest a missing meter change; the time-
  signature grouping is ambiguous, so no rest was added.
- `YuE2_ABC_00031.txt`: Measure 20 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00032.txt`: Unsupported ABC notation at line 96, near ',1,4-'.
- `YuE2_ABC_00039.txt`: Unsupported ABC notation at line 54, near '""'.
- `YuE2_ABC_00040.txt`: Measure 43 in ins is short by 3/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00042.txt`: Unsupported ABC notation at line 40, near "'3".
- `YuE2_ABC_00045.txt`: Measure 29 in ins is short by 1/16 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00048.txt`: Unsupported ABC notation at line 52, near '='.
- `YuE2_ABC_00049.txt`: Unsupported ABC notation at line 103, near '='.
- `YuE2_ABC_00053.txt`: Invalid Ins meter field 'F#' at line 51; the instrumental grid is ambiguous.
- `YuE2_ABC_00054.txt`: Measure 17 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00060.txt`: Unsupported chord symbol '"A#/ug"' at line 23.
- `YuE2_ABC_00063.txt`: Unsupported ABC notation at line 72, near "'16-".
- `YuE2_ABC_00064.txt`: Measure 36 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00067.txt`: Vocal contains overlapping pitched notes at 35 and 295/8.
- `YuE2_ABC_00068.txt`: Measure 13 in ins is short by 3/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00069.txt`: Measure 18 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00070.txt`: Measure 30 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00071.txt`: Measure 20 in vocal is short by 1/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00072.txt`: Unsupported ABC notation at line 53, near "'16-".
- `YuE2_ABC_00073.txt`: Measure 57 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00074.txt`: Unsupported ABC notation at line 135, near 'im'.
- `YuE2_ABC_00076.txt`: Measure 56 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00077.txt`: Unsupported ABC notation at line 52, near '""'.
- `YuE2_ABC_00080.txt`: Unterminated chord symbol at line 65.
- `YuE2_ABC_00082.txt`: Unterminated chord symbol at line 86.
- `YuE2_ABC_00085.txt`: Unsupported chord symbol '"A#/sus2"' at line 60.
- `YuE2_ABC_00090.txt`: Unsupported ABC notation at line 90, near ',,1,,,,4,,,,3,,,,,,,,,,,'.
- `YuE2_ABC_00097.txt`: Unsupported chord symbol '"A#/ug"' at line 28.
- `YuE2_ABC_00101.txt`: Unterminated chord symbol at line 67.
- `YuE2_ABC_00103.txt`: Unsupported ABC notation at line 74, near "'3'2".
- `YuE2_ABC_00109.txt`: Unsupported ABC notation at line 64, near "'4".
- `YuE2_ABC_00113.txt`: Measure 18 in ins is short by 1/8 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00114.txt`: Measure 21 in ins is short by 1/16 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00123.txt`: Unsupported ABC notation at line 77, near '""'.
- `YuE2_ABC_00125.txt`: Unsupported ABC notation at line 28, near ',3,3'.
- `YuE2_ABC_00127.txt`: Unsupported ABC notation at line 53, near ',2'.
- `YuE2_ABC_00133.txt`: Unsupported ABC notation at line 37, near ',4-'.
- `YuE2_ABC_00134.txt`: Measure 48 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00136.txt`: Unsupported chord symbol '"A#/e#"' at line 118.
- `YuE2_ABC_00137.txt`: Unsupported ABC notation at line 55, near ',,6'.
- `YuE2_ABC_00148.txt`: Unsupported Ins key change
  'D#maj7)"^^ccc^^fa^^gac\'^babc\'baga|bagabagabagabaga|bagabagabagabaga|bage^^cBAGz8|' at line 44;
  instrumental pitches cannot be inferred safely.
- `YuE2_ABC_00154.txt`: Measure 53 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00155.txt`: Measure 12 in vocal is short by 11/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00157.txt`: Unsupported ABC notation at line 42, near 'Vo'.
- `YuE2_ABC_00160.txt`: Measure 51 in vocal is short by 1/8 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00162.txt`: Measure 79 in ins is short by 1/2 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00163.txt`: Unsupported ABC notation at line 37, near '/'.
- `YuE2_ABC_00164.txt`: Measure 52 in vocal is short by 1/8 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00168.txt`: Measure 66 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00169.txt`: Unsupported ABC notation at line 96, near ',12'.
- `YuE2_ABC_00176.txt`: Unsupported ABC notation at line 67, near '32-'.
- `YuE2_ABC_00180.txt`: Invalid Ins meter field 'C: Vocal' at line 42; the instrumental grid is ambiguous.
- `YuE2_ABC_00181.txt`: Unsupported ABC notation at line 78, near 'Ins'.
- `YuE2_ABC_00185.txt`: Unsupported ABC notation at line 41, near 'im7'.
- `YuE2_ABC_00186.txt`: Unsupported ABC notation at line 29, near '-'.
- `YuE2_ABC_00187.txt`: Measure 19 in vocal is short by 7/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00189.txt`: Measure 46 in ins is short by 3/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00190.txt`: Unsupported ABC notation at line 73, near "'2".
- `YuE2_ABC_00194.txt`: Measure 34 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00198.txt`: Measure 14 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00200.txt`: Vocal contains overlapping pitched notes at 313/4 and 317/4.
- `YuE2_ABC_00206.txt`: Aligned short measures in Vocal and Ins suggest a missing meter change; the time-
  signature grouping is ambiguous, so no rest was added.
- `YuE2_ABC_00207.txt`: Measure 10 in vocal is short by 1/16 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00213.txt`: Unsupported ABC notation at line 33, near 'Ins'.
- `YuE2_ABC_00220.txt`: Unsupported chord symbol '"Fm(maj9/5)"' at line 113.
- `YuE2_ABC_00229.txt`: Measure 15 in vocal is short by 1/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00230.txt`: Unsupported ABC notation at line 29, near 'm(m'.
- `YuE2_ABC_00232.txt`: Unsupported ABC notation at line 106, near 'Ins'.
- `YuE2_ABC_00234.txt`: Unsupported ABC notation at line 89, near '#'.
- `YuE2_ABC_00238.txt`: Unsupported ABC notation at line 88, near '""'.
- `YuE2_ABC_00239.txt`: Measure 56 in vocal is short by 1/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00242.txt`: Aligned short measures in Vocal and Ins suggest a missing meter change; the time-
  signature grouping is ambiguous, so no rest was added.
- `YuE2_ABC_00244.txt`: Measure 35 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00248.txt`: Measure 18 in ins is short by 1/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00253.txt`: Vocal contains overlapping pitched notes at 185/8 and 93/4.
- `YuE2_ABC_00254.txt`: Measure 42 in ins is short by 1/16 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00255.txt`: Vocal contains overlapping pitched notes at 141/8 and 173/8.
- `YuE2_ABC_00258.txt`: Invalid Ins meter field 'Dm"f2g2d12-d2z6|Z3|' at line 70; the instrumental grid is
  ambiguous.
- `YuE2_ABC_00259.txt`: Measure 17 in vocal is short by 1/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00264.txt`: Measure 56 in ins is short by 3/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00265.txt`: Measure 73 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00270.txt`: Unsupported ABC notation at line 32, near '-'.
- `YuE2_ABC_00272.txt`: Unsupported ABC notation at line 28, near ',16-'.
- `YuE2_ABC_00273.txt`: Measure 14 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00274.txt`: Measure 15 in ins is short by 1/16 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00276.txt`: Measure 19 in vocal is short by 3/16 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00281.txt`: Measure 59 in ins is short by 1/8 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00284.txt`: Measure 39 in vocal is short by 1/8 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00289.txt`: Unsupported ABC notation at line 99, near "'2".
- `YuE2_ABC_00296.txt`: Unterminated chord symbol at line 57.
- `YuE2_ABC_00297.txt`: Unsupported ABC notation at line 44, near "'-".
- `YuE2_ABC_00299.txt`: Measure 14 in vocal is short by 1/16 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00300.txt`: Unsupported ABC notation at line 80, near "''8".
- `YuE2_ABC_00303.txt`: Measure 30 in vocal overruns by more than 4 measures.
- `YuE2_ABC_00304.txt`: Measure 42 in ins has an overhanging note that overlaps another note; no candidate
  preserves Ins and the monophonic timeline.
- `YuE2_ABC_00305.txt`: Unsupported ABC notation at line 51, near 'sus2'.
- `YuE2_ABC_00307.txt`: Unsupported ABC notation at line 45, near 'im7'.
- `YuE2_ABC_00311.txt`: Unsupported ABC notation at line 26, near ',8'.
- `YuE2_ABC_00312.txt`: Measure 43 in vocal is short by 3/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00313.txt`: Unsupported ABC notation at line 75, near "'2".
- `YuE2_ABC_00315.txt`: Unsupported ABC notation at line 56, near ',,4'.
- `YuE2_ABC_00326.txt`: Unsupported ABC notation at line 38, near '3'.
- `YuE2_ABC_00327.txt`: Measure 49 in ins is short by 5/32 whole-note units and ends with a tie; no aligned
  Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.

## Rejection categories

| Category | SBL | New converter |
|---|---:|---:|
| chord symbols | 2 | 9 |
| measure duration or alignment | 90 | 21 |
| meter or key metadata | 12 | 4 |
| other validation error | 0 | 4 |
| ties | 114 | 30 |
| unsupported ABC token | 21 | 45 |
| voice structure | 4 | 0 |
