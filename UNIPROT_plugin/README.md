# UniProt Structures PyMOL Plugin

Download UniProt-linked PDB/mmCIF structures and AlphaFold models, organize them by
experimental method, and align one mapped protein chain per structure.

## Activate the updated source

In the PyMOL command line:

```pymol
run H:/My Drive/VSCode_Github/Pymol/UNIPROT_plugin/__init__.py
uniprot_structures_gui
```

For permanent installation, reinstall this source through Plugin > Plugin Manager >
Install New Plugin, then restart PyMOL. An already installed copy will not update
just because the source on H: changed.

## GUI

Open Plugin > UniProt Structures, or use `uniprot_structures_gui`.

- Enter a UniProt accession (`P06730`) or entry name (`IF4E_HUMAN`).
- Alternatively, **Browse...** beside **List file** selects a TXT, CSV or TSV file.
  A selected file takes priority over the single-entry field; clear it to use that field.
- Choose a **Download folder** for persistent files, or leave blank for the system
  temporary cache. Each protein has its own subfolder.
- Choose PDB, AlphaFold, or both, and an alignment method (`align`, `super`, `cealign`).
- Set **PDB limit per protein**; zero means all available PDB references.
- For many proteins/structures, use **Download only** to save files without adding
  objects to PyMOL or consuming memory for the full collection.
- Click **Start**. The window stays responsive, displays elapsed time, a progress
  bar, and an activity log. The PyMOL console receives the same activity messages.
- **Cancel** stops cooperatively. Finished files and objects are retained. A running
  network read may take up to its timeout (60 seconds); a PyMOL load or alignment
  must finish before cancellation takes effect. Closing a busy dialog requests cancellation.

Progress is reported separately for each protein: structures processed / total,
percentage, remaining, freshly downloaded, reused from cache, download failures,
and loaded objects. Transfer messages also show MB received and, when the server
supplies a size, that file's transfer percentage. **Processed** includes failed jobs;
100% processed does not mean every structure downloaded or aligned successfully.
The final batch message includes the error count. Detailed failures remain in the log.

## Input files

A plain TXT list can contain one entry per line or whitespace-separated entries:

```text
# UniProt accessions or entry names
P06730
IF4E_HUMAN
P04637
```

CSV/TSV can contain a recognized UniProt column, for example:

```csv
accession,note
P06730,eIF4E
P04637,p53
```

Accepted column headings include `uniprot`, `uniprot_id`, `uniprot_accession`,
`accession`, `entry`, and `entry_name` (spaces or hyphens also accepted).
Without a recognized heading, the first CSV/TSV column is used. UTF-8 files,
including UTF-8 BOM, are supported. Blank lines, lines beginning with `#`, and
exact duplicate entries are ignored. Protein names as free text and gene symbols
are not resolved: use UniProt accessions or UniProt entry names.

## Visualization and alignment

Each PDB object keeps **one UniProt-mapped protein chain**: chain A when A is mapped
and present, otherwise the first available mapped chain. If UniProt provides no
chain mapping, the first protein chain is used, preferring A. If a supplied mapping
matches no protein chain, the structure is skipped and the reason is logged.
Unrelated chains, ligands and waters are removed from the PyMOL object; cached
coordinate files remain complete. All model states, including NMR ensembles, are
retained. The selected chain is reported in the activity log.

Structures are aligned as they load, using the protein chain against a single
protein chain in the AlphaFold reference. If AlphaFold is absent, disabled, or fails
to load, the first successfully loaded PDB becomes the reference. Alignment failures
are logged and the affected objects are hidden and disabled, so they do not appear
as if they were successfully fitted. Successful structures are shown as cartoons.
With alignment switched off, selected chains are displayed without fitting.

The root group is the UniProt entry name, with `AF_<entry_name>` directly inside it.
Experimental PDB objects use their PDB codes and are grouped under `NMR`, `X-ray`,
`EM`, or `Other_methods`. Repeated runs use unique names to preserve existing objects.
Only the objects created by the current job have their representations changed.

## Commands

Existing positional syntax is preserved, with new trailing options:

```pymol
uniprot_structures query, include_pdb, include_alphafold, align, alignment_method, limit, download_only, cache_dir
```

Defaults: both sources enabled, alignment enabled, method `align`, limit `0`,
`download_only=0`, and the system temporary cache.

```pymol
uniprot_structures P06730
uniprot_structures P06730, 1, 1, 1, align, 10
uniprot_structures P06730, 1, 1, 1, align, 0, 1, D:/UniProt_cache/P06730
```

The last example downloads all selected files without loading them into PyMOL.

Batch file command (limit applies to each protein):

```pymol
uniprot_structures_file H:/My Drive/uniprot_list.txt, 10
uniprot_structures_file H:/My Drive/uniprot_list.txt, 0, 1, D:/UniProt_cache
```

GUI jobs run in a background worker. Command-line calls run synchronously and print
progress to the console. Only one job can run at a time. Batch entries run sequentially;
an entry failure is logged and does not prevent later entries from being attempted.

## Downloads and memory

The default cache is `<system temp>/pymol_uniprot_structures/<entry_name>`.
The exact path is reported. RCSB downloads try PDB first, then mmCIF on a 404.
Downloads use fixed-size buffers and atomic file replacement, so interrupted or
failed transfers do not leave a partial file that is reused as a completed download.
Existing nonempty files are reused; older cache files are not structurally validated.

Pruning chains in place avoids a second full PyMOL object, but PyMOL must initially
load each complete coordinate file. Large assemblies and NMR ensembles can still
require substantial memory. Loading many structures retains them in the scene;
the worker and progress bar do not impose a memory ceiling. Use a modest PDB limit
for visualization and **Download only** for bulk acquisition.

## Verification

`test_plugin.py` covers list parsing, progress and cache accounting, atomic-download
failure/cancellation handling, batch failure recovery, and actual headless PyMOL
chain selection, fitting, and failed-alignment visibility. Run with a Python
interpreter that provides PyMOL (for example PyMOL's bundled Python):

```powershell
& 'C:\Users\geras\AppData\Local\Schrodinger\PyMOL2\python.exe' -B 'H:\My Drive\VSCode_Github\Pymol\UNIPROT_plugin\test_plugin.py'
```

## Data and API sources

- [UniProt REST API](https://www.uniprot.org/help/api_queries)
- [AlphaFold DB](https://alphafold.ebi.ac.uk/)
- [RCSB coordinate downloads](https://files.rcsb.org/download/)
- [PyMOL command implementation](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/importing.py)
