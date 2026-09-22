"""Calculate raw RMSD between selected atoms in two requested PDB frames.

Only the requested source and target frames are extracted from disk. Biopython
parses those two small frame blocks; the remaining models are never constructed
in memory. Coordinates are compared exactly as stored--no rotation, translation,
or structural fit is performed.

Frames may come from the same PDB or different PDBs. Both MODEL/ENDMDL files and
concatenated single-structure PDBs separated by END records are supported.

Dependency: Biopython (and its required NumPy dependency).
"""

import argparse
import os
import sys
from collections import OrderedDict
from io import StringIO
from pathlib import Path

import numpy as np
from Bio.PDB import PDBParser


BACKBONE_ATOMS = {"N", "CA", "C", "O", "OXT"}
COORDINATE_RECORDS = {"ATOM", "HETATM"}


def parse_number_list(text, flag, parser):
    """Parse a comma/range list such as 37,50-60,100."""
    values = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token[1:]:
            split_at = token.index("-", 1)
            try:
                first = int(token[:split_at])
                last = int(token[split_at + 1 :])
            except ValueError:
                parser.error(f"Invalid range '{token}' in {flag}.")
            if first > last:
                parser.error(f"Invalid descending range '{token}' in {flag}.")
            values.update(range(first, last + 1))
        else:
            try:
                values.add(int(token))
            except ValueError:
                parser.error(f"Invalid residue '{token}' in {flag}.")
    if not values:
        parser.error(f"{flag} is empty.")
    return frozenset(values)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Calculate raw RMSD between selected atoms in one source frame and one target frame."
    )
    parser.add_argument("-s", "--source", type=Path, required=True, help="Source/mobile PDB file.")
    parser.add_argument("-t", "--target", type=Path, required=True, help="Target/fixed PDB file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional text output path; omit or use '-' for standard output.",
    )
    parser.add_argument(
        "--source-frame",
        type=int,
        help="1-based source frame; defaults to --target-frame.",
    )
    parser.add_argument(
        "--target-frame",
        type=int,
        default=1,
        help="1-based target frame (default: 1).",
    )
    parser.add_argument(
        "--atom-scope",
        choices=("ca", "backbone", "full"),
        default="ca",
        help="Atoms included in RMSD: ca, backbone, or full (default: ca).",
    )
    parser.add_argument(
        "--chain",
        help="Target chain; also used for source unless --source-chain is given.",
    )
    parser.add_argument(
        "--source-chain",
        help="Source chain; defaults to --chain.",
    )
    parser.add_argument(
        "--residues",
        help="Residue numbers/ranges applied to both structures, e.g. 37,50-60,100.",
    )
    args = parser.parse_args()
    if args.target_frame < 1:
        parser.error("--target-frame must be at least 1.")
    if args.source_frame is not None and args.source_frame < 1:
        parser.error("--source-frame must be at least 1.")
    args.source_frame = args.source_frame if args.source_frame is not None else args.target_frame
    args.residue_numbers = (
        parse_number_list(args.residues, "--residues", parser) if args.residues else None
    )
    return args


def same_path(first, second):
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def extract_frame_blocks(path, requested_frames):
    """Extract only requested 1-based frame blocks in one sequential scan."""
    requested = set(requested_frames)
    blocks = {frame: [] for frame in requested}
    completed = set()
    saw_model = False
    current_frame = None
    frame_count = 0

    with open(path, "r", encoding="ascii", errors="replace", newline="") as handle:
        for line in handle:
            record = line[:6].strip()
            if record == "MODEL":
                saw_model = True
                frame_count += 1
                current_frame = frame_count
                if current_frame in requested:
                    blocks[current_frame].append(line)
                continue

            if not saw_model and record in COORDINATE_RECORDS and current_frame is None:
                frame_count += 1
                current_frame = frame_count

            if current_frame in requested and record in {"ATOM", "HETATM", "TER"}:
                blocks[current_frame].append(line)

            frame_ended = record == "ENDMDL" or (not saw_model and record == "END")
            if frame_ended and current_frame is not None:
                if current_frame in requested:
                    blocks[current_frame].append(line)
                    completed.add(current_frame)
                current_frame = None
                if completed == requested:
                    break

        if current_frame in requested and blocks[current_frame]:
            completed.add(current_frame)

    missing = requested - completed
    if missing:
        raise ValueError(
            f"Frame(s) {sorted(missing)} not found in {path}; detected {frame_count} frame(s)."
        )
    return blocks


def parse_frame(lines, structure_id):
    """Use Biopython to parse one previously extracted frame block."""
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure(structure_id, StringIO("".join(lines)))
    try:
        return next(structure.get_models())
    except StopIteration as exc:
        raise ValueError(f"The selected {structure_id} frame contains no parseable atoms.") from exc


def atom_in_scope(atom_name, atom_scope):
    if atom_scope == "ca":
        return atom_name == "CA"
    if atom_scope == "backbone":
        return atom_name in BACKBONE_ATOMS
    return True


def select_atoms(model, chain_id, residue_numbers, atom_scope):
    """Return an ordered residue map containing selected Biopython Atom objects."""
    selected = OrderedDict()
    for chain in model:
        if chain_id is not None and chain.id != chain_id:
            continue
        for residue in chain:
            if residue.id[0] != " ":
                continue
            if residue_numbers is not None and residue.id[1] not in residue_numbers:
                continue
            atom_map = selected.setdefault((chain.id, residue.id[1], residue.id[2]), {})
            for atom in residue:
                name = atom.get_name()
                if atom_in_scope(name, atom_scope):
                    atom_map[name] = atom
            if not atom_map:
                selected.pop((chain.id, residue.id[1], residue.id[2]), None)
    return selected


def paired_coordinates(target_selection, source_selection, ignore_chain):
    """Pair selected atoms by residue identity and atom name."""
    def flatten(selection):
        flattened = {}
        for (chain, residue_number, insertion_code), atoms in selection.items():
            for atom_name, atom in atoms.items():
                residue_key = (residue_number, insertion_code) if ignore_chain else (
                    chain,
                    residue_number,
                    insertion_code,
                )
                flattened[(residue_key, atom_name)] = atom.get_coord()
        return flattened

    target_atoms = flatten(target_selection)
    source_atoms = flatten(source_selection)
    shared = sorted(set(target_atoms) & set(source_atoms))
    if not shared:
        raise ValueError("The source and target selections have no matching atoms.")
    target_coordinates = np.asarray([target_atoms[key] for key in shared], dtype=np.float64)
    source_coordinates = np.asarray([source_atoms[key] for key in shared], dtype=np.float64)
    return target_coordinates, source_coordinates, len(shared)


def calculate_raw_rmsd(target_coordinates, source_coordinates):
    squared_distances = np.sum((source_coordinates - target_coordinates) ** 2, axis=1)
    return float(np.sqrt(np.mean(squared_distances)))


def render_result(args, rmsd, matched_atoms):
    return (
        f"RMSD: {rmsd:.6f} A\n"
        f"Matched atoms: {matched_atoms}\n"
        f"Source frame: {args.source_frame}\n"
        f"Target frame: {args.target_frame}\n"
        f"Atom scope: {args.atom_scope}\n"
    )


def main():
    args = parse_args()
    source = args.source.resolve()
    target = args.target.resolve()
    if not source.is_file():
        sys.exit(f"Source PDB not found: {source}")
    if not target.is_file():
        sys.exit(f"Target PDB not found: {target}")

    try:
        if same_path(source, target):
            blocks = extract_frame_blocks(source, {args.source_frame, args.target_frame})
            source_model = parse_frame(blocks[args.source_frame], "source")
            target_model = parse_frame(blocks[args.target_frame], "target")
        else:
            source_block = extract_frame_blocks(source, {args.source_frame})[args.source_frame]
            target_block = extract_frame_blocks(target, {args.target_frame})[args.target_frame]
            source_model = parse_frame(source_block, "source")
            target_model = parse_frame(target_block, "target")

        source_chain = args.source_chain if args.source_chain is not None else args.chain
        source_selection = select_atoms(
            source_model, source_chain, args.residue_numbers, args.atom_scope
        )
        target_selection = select_atoms(
            target_model, args.chain, args.residue_numbers, args.atom_scope
        )
        if not source_selection or not target_selection:
            raise ValueError("The source or target atom selection is empty.")

        ignore_chain = args.chain is not None or source_chain is not None
        target_coordinates, source_coordinates, matched_atoms = paired_coordinates(
            target_selection, source_selection, ignore_chain
        )
        rmsd = calculate_raw_rmsd(target_coordinates, source_coordinates)
        result = render_result(args, rmsd, matched_atoms)
    except (OSError, ValueError) as exc:
        sys.exit(f"Error: {exc}")

    if args.output is None or str(args.output) == "-":
        sys.stdout.write(result)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")
        print(f"RMSD result written to: {args.output}")


if __name__ == "__main__":
    main()
