import json, collections
from pathlib import Path

data = json.loads(Path(r'C:\CC4E\.aviator\repository_search.json').read_text(encoding='utf-8'))
flush = data[:86]

# ---- CATEGORY 4: Same file, multiple lines ----
print('=== CAT 4: Same file, multiple lines (one flush) ===')
file_lines = collections.defaultdict(set)
for r in flush:
    if r['line_number'] > 0:
        file_lines[r['file_path']].add(r['line_number'])
multi_line = {f: ls for f, ls in file_lines.items() if len(ls) > 1}
print(f'Files with >1 distinct line matched: {len(multi_line)}')
for f, ls in sorted(multi_line.items(), key=lambda x: -len(x[1]))[:5]:
    print(f'  lines {sorted(ls)} -> {f}')

# ---- CATEGORY 5: Evidence merging ----
print()
print('=== CAT 5: EvidenceItems produced per _query_repository_search() call ===')

H1_LITERALS = {'26.2','26.3','260200','260300','26.2.0','26.3.0'}
H1_SYMBOLS  = {'ghs_version','ghshelpversion','helpversion','app_version','version','genericconstants'}
H2_LITERALS = {'26.2','260200','run-job','helm'}
H2_SYMBOLS  = {'run-job','helm','values.yaml','deployment'}

def get_hyp_ids(r):
    q = r['query'].lower()
    qt = r['query_type']
    ids = []
    if qt == 'literal':
        if q in {x.lower() for x in H1_LITERALS}: ids.append('H1')
        if q in {x.lower() for x in H2_LITERALS}: ids.append('H2')
    else:
        if q in H1_SYMBOLS: ids.append('H1')
        if q in H2_SYMBOLS: ids.append('H2')
    return ids or ['?']

evidence_items = []
seen_keys = set()
visited = set()

for r in flush:
    for hyp_id in get_hyp_ids(r):
        fp = r['file_path']
        key = hyp_id + ':' + fp
        if fp in visited or key in seen_keys:
            continue
        evidence_items.append((hyp_id, fp))
        seen_keys.add(key)

unique_files_in_evidence = set(f for _, f in evidence_items)
print(f'Raw EvidenceItems (one call)       : {len(evidence_items)}')
print(f'Unique file_paths in EvidenceItems : {len(unique_files_in_evidence)}')
print(f'Duplicate EvidenceItems (same file, diff hyp): {len(evidence_items) - len(unique_files_in_evidence)}')
file_counts = collections.Counter(f for _, f in evidence_items)
dup_files = {f: c for f, c in file_counts.items() if c > 1}
print(f'Files with duplicate EvidenceItems: {len(dup_files)}')
for f in sorted(dup_files):
    hyps = [h for h, fp in evidence_items if fp == f]
    print(f'  {hyps} -> {f}')

change_group_unique = list(dict.fromkeys(f for _, f in evidence_items))
print(f'After change_group dict.fromkeys   : {len(change_group_unique)} unique files')

# ---- run-job.sh deep analysis ----
print()
print('=== run-job.sh DEEP ANALYSIS ===')
TARGET = 'project-service/helm/static/run-job.sh'
sh_records = [r for r in data if r['file_path'] == TARGET]
print(f'Total records in trace file: {len(sh_records)}')
print()

# Per query breakdown
query_counts = collections.Counter((r['query'], r['query_type']) for r in sh_records)
print('Per-query match counts (all 18 flushes):')
for (q, qt), c in sorted(query_counts.items(), key=lambda x: -x[1]):
    print(f'  {c:3d}x  [{qt}] query={q!r}')

print()
print('One flush sample (first 3 records for run-job.sh):')
sh_flush = [r for r in flush if r['file_path'] == TARGET]
for r in sh_flush:
    hyps = get_hyp_ids(r)
    print(f'  hyp={hyps}  query={r["query"]!r}  type={r["query_type"]}  line={r["line_number"]}')
    print(f'  matched: {r["matched_text"]}')
    print(f'  score: {r["confidence"]}')

print()
print('EvidenceItems created for run-job.sh (one call):')
sh_evidence = [(h, f) for h, f in evidence_items if f == TARGET]
for h, f in sh_evidence:
    print(f'  EvidenceItem(source=repository_search, file={f}, hyp_id={h})')
print(f'Total EvidenceItems for run-job.sh in one call: {len(sh_evidence)}')

# ---- Duplication origin table ----
print()
print('=== DUPLICATION ORIGIN SUMMARY ===')
print()
print('Stage                             | run-job.sh | All files | Mechanism')
print('-'*75)
print(f'RepositorySearchEngine._append_trace  | 3/flush    | 86/flush  | same literal in 2 hyps + filename')
print(f'flush_trace() cross-call accumulation | 3x18=54    | 86x18=1548| no dedup on merge to file')
print(f'_query_repository_search() EvidenceItem| 2/call    | 71/call   | seen_keys keyed on (hyp,file) not file')
print(f'collect() cross-iteration visited set | 1/collect  | 60/collect| visited blocks re-add after iter 1')
print(f'grounded_understanding change_group   | 1 entry    | 60 entries| dict.fromkeys deduplicates file paths')
