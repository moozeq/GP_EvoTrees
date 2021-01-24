#!/usr/bin/env python3
import argparse
import itertools
import json
import logging
import shutil
import subprocess
from string import ascii_lowercase

import requests
from collections import defaultdict
from pathlib import Path
from typing import List, Dict, Tuple

from Bio import Phylo
from Bio.Phylo.Consensus import majority_consensus
from Bio.Align.Applications import MuscleCommandline
from Bio.SeqRecord import SeqRecord
from joblib import Parallel, delayed


class RecUtils:
    @staticmethod
    def read_records(filename: str):
        """Get records from file as Seqs objects"""
        from Bio import SeqIO
        seqs = [record for record in SeqIO.parse(filename, 'fasta')]
        return seqs

    @staticmethod
    def save_fasta_records(recs, filename):
        """Save records as single fasta"""
        from Bio import SeqIO
        SeqIO.write(recs, filename, 'fasta')
        return filename

    @staticmethod
    def count_records(filename: str):
        """Count how many records in fasta file"""
        from Bio import SeqIO
        return sum(True for _ in SeqIO.parse(filename, 'fasta'))

    @staticmethod
    def load_species(filename: str) -> List[str]:
        with open(filename) as f:
            return json.load(f)

    @staticmethod
    def normalize_species(sp: str) -> str:
        not_valid = [' ', '-', '/', '(', ')', '#', ':', ',', ';', '[', ']', '\'', '"', '___', '__']
        for ch in not_valid:
            sp = sp.replace(ch, '_')
        return sp

    @staticmethod
    def generate_ids():
        for size in itertools.count(1):
            for s in itertools.product(ascii_lowercase, repeat=size):
                yield ''.join(s).upper()

    @staticmethod
    def retrieve_species_names(tree_file, sp_map: Dict[str, str], out: str, rm_zero_lengths: bool = False) -> str:
        def remove_zero_lengths(tree_f):
            with open(tree_f) as f:
                tree_str = f.read()
            tree_str = tree_str.replace(':0.00000', '')
            with open(tree_f, 'w') as f:
                f.write(tree_str)

        from Bio.Phylo.Newick import Tree
        try:
            tree: Tree = Phylo.read(tree_file, 'newick')
            terms = tree.get_terminals()
            for clade in terms:
                clade.name = sp_map[clade.name]
            Phylo.write(tree, out, 'newick')
            # remove lengths which cause issues when viewing tree
            if rm_zero_lengths:
                remove_zero_lengths(out)
            return out
        except Exception as e:
            logging.info(f'Could not retrieve species names for: {tree_file}, error = {str(e)}')
            return ''


class Tools:
    @staticmethod
    def make_RAxML_trees(aligned_fasta: str, output_dir: str, sub_model: str = 'PROTGAMMAGTR') -> (str, str):
        """Calculate trees using RAxML, returns filenames for ML and parsimony trees"""
        name = Path(aligned_fasta).name[:-len('.fasta')]
        output_dir = f'{output_dir}/{name}'
        (path := Path(output_dir)).mkdir(exist_ok=True)
        ml_tree_nwk, mp_tree_nwk = f'{output_dir}/RAxML_bestTree.results', f'{output_dir}/RAxML_parsimonyTree.results'

        # already exist
        if Path(ml_tree_nwk).exists() and Path(mp_tree_nwk).exists():
            return ml_tree_nwk, mp_tree_nwk, name

        cline = f'raxml -s {aligned_fasta} -w {path.absolute()} -n results -m {sub_model} -p 12345'
        try:
            subprocess.run(str(cline).split(), check=True)
            return ml_tree_nwk, mp_tree_nwk, name
        except subprocess.CalledProcessError:
            logging.error(f'Could not build ML an MP trees for: {aligned_fasta}')
            shutil.rmtree(path.absolute())
            return ''

    @staticmethod
    def make_ninja_tree(aligned_fasta: str, output_dir: str) -> str:
        """Calculate neighbour-joining tree using ninja, returns filename for NJ tree"""
        name = Path(aligned_fasta).name[:-len('.fasta')]
        nj_tree_nwk = f'{output_dir}/{name}'

        # already exist
        if Path(nj_tree_nwk).exists():
            return nj_tree_nwk

        cline = f'ninja --in {aligned_fasta} --out {nj_tree_nwk}'
        try:
            subprocess.run(str(cline).split(), check=True)
            return nj_tree_nwk
        except subprocess.CalledProcessError:
            logging.error(f'Could not build NJ tree for: {aligned_fasta}')
            return ''

    @staticmethod
    def make_clann_super_tree(trees_dir: str, out_tree_nwk: str) -> str:
        """Make supertree using clann"""
        if Path(out_tree_nwk).exists():
            return out_tree_nwk

        merged_trees = f'{trees_dir}/_alltrees.ph'
        cmds_file = f'{trees_dir}/_clanncmds'

        try:
            with open(merged_trees, 'w') as f:
                for tree in Path(trees_dir).glob('*.nwk'):
                    with open(tree) as ft:
                        tree_w_only_one_nl = ft.read().replace('\n', '')
                        tree_w_only_one_nl = f'{tree_w_only_one_nl}\n'
                        f.write(tree_w_only_one_nl)

            with open(cmds_file, 'w') as cmds:
                cmds.write(f'execute; hs swap=spr maxswaps=10000 nreps=3 weight=equal savetrees={out_tree_nwk}')

            cline = f'clann -ln -c {cmds_file} {merged_trees}'
            subprocess.run(str(cline).split(), check=True)
            return out_tree_nwk
        except subprocess.CalledProcessError as e:
            logging.error(f'Could not build super-tree for: {trees_dir}, err = {str(e)}')
            return ''

    @staticmethod
    def make_phylo_consensus_tree(trees_dir: str, out_tree_nwk: str) -> str:
        """Make consensus tree using biopython phylo package"""
        if Path(out_tree_nwk).exists():
            return out_tree_nwk

        trees_files = Path(trees_dir).glob(f'*.nwk')
        try:
            trees = [Phylo.read(tf, 'newick') for tf in trees_files]
            majority_tree = majority_consensus(trees)
            Phylo.write(majority_tree, out_tree_nwk, 'newick')
        except Exception as e:
            logging.error(f'Could not build consensus tree for: {trees_dir}, err = {str(e)}')
            return ''
        return majority_tree

    @staticmethod
    def mmseqs2(merged_fasta: str, out: str):
        if not Path((cluster_file := f'{out}/_all_seqs.fasta')).exists():
            subprocess.run(f'mmseqs easy-cluster {merged_fasta} mmseqs2 {out}'.split())
            Path('mmseqs2_all_seqs.fasta').replace(cluster_file)
            Path('mmseqs2_cluster.tsv').replace(f'{out}/_cluster.tsv')
            Path('mmseqs2_rep_seq.fasta').replace(f'{out}/_rep_seq.fasta')

        return cluster_file


class Uniprot:
    @staticmethod
    def get_proteome_id_by_organism(organism: str) -> str:
        query = f'query=organism:{organism}&format=list&sort=score'
        url = f'https://www.uniprot.org/proteomes/?{query}'
        try:
            ids = requests.get(url)
            ids = ids.content.decode()
            if not ids:
                raise Exception('empty list')
            pid = ids.splitlines()[0]
            if not pid.startswith('UP'):
                raise Exception(f'wrong pid = {pid}')
            logging.info(f'Get proteome ID: {organism} -> {pid}')
            return pid
        except Exception as e:
            logging.error(f'Could not download proteome IDs list for: {organism}, error = {str(e)}')
            return ''

    @staticmethod
    def download_proteomes_ids(f: str, o: str) -> str:
        query = f'query=taxonomy:{f}&format=tab&sort=score'
        url = f'https://www.uniprot.org/proteomes/?{query}'
        try:
            if Path(o).exists():
                return o
            ids = requests.get(url)
            ids = ids.content.decode()
            if not ids:
                raise Exception('empty list')
            logging.info(f'Downloaded proteomes IDs list: {len(ids.splitlines()) - 1}')
            with open(o, 'w') as fp:
                fp.write(ids)
            return o
        except Exception as e:
            logging.error(f'Could not download proteomes IDs list for: {f}, error = {str(e)}')
            return ''

    @staticmethod
    def download_proteome(pid: str, org: str, o_dir: str):
        query = f'query=proteome:{pid}&format=fasta&compress=no'
        url = f'https://www.uniprot.org/uniprot/?{query}'
        try:
            if Path(pfile := f'{o_dir}/{RecUtils.normalize_species(org)}.fasta').exists():
                return pfile
            ids = requests.get(url)
            ids = ids.content.decode()
            if not ids:
                raise Exception('empty proteome')
            logging.info(f'Downloaded proteome for: {org}')
            with open(pfile, 'w') as fp:
                fp.write(ids)
            return pfile
        except Exception as e:
            logging.error(f'Could not download proteome for: {org}, error = {str(e)}')
            return ''


def download_proteomes_ete3(all_species: List[str], out: str):
    def get_tax_id(s: str) -> str:
        def get_tax_id_from_ete3(ete3_out: str) -> str:
            first_line = ete3_out.splitlines()[1]  # omit header
            ete3_tid = first_line.split('\t')[0]  # tax id is first
            return ete3_tid

        ete_cmd = ['ete3', 'ncbiquery', '--info', '--search']
        try:
            ete_s_cmd = ete_cmd + [s]
            ete_out = subprocess.run(ete_s_cmd, check=True, capture_output=True)
            ete3_tax_id = get_tax_id_from_ete3(ete_out.stdout.decode())
            logging.info(f'Translated: {s} -> {ete3_tax_id}')
            return ete3_tax_id
        except subprocess.CalledProcessError:
            logging.error(f'Could not translate to tax ID: {s}')
            return ''

    if not Path((trans_tax := f'{out}/_transtax.json')).exists():
        proteomes_taxids = {
            RecUtils.normalize_species(species): tax_id
            for species in all_species
            if (tax_id := get_tax_id(species))
        }
        logging.info(f'Translated names to tax IDs: {len(proteomes_taxids)}/{len(all_species)}')
        with open(trans_tax, 'w') as f:
            json.dump(proteomes_taxids, f, indent=4)
    else:
        with open(trans_tax) as f:
            proteomes_taxids = json.load(f)

    proteomes_filenames = [
        proteome_filename
        for species, tax_id in proteomes_taxids.items()
        if (proteome_filename := Uniprot.download_proteome(tax_id, f'{out}/{RecUtils.normalize_species(species)}.fasta'))
    ]

    valid_files = [
        file
        for file in proteomes_filenames
        if Path(file) and RecUtils.count_records(file)
    ]

    logging.info(f'Downloaded proteomes: {len(valid_files)}/{len(all_species)}')
    return proteomes_filenames


def download_proteomes_by_names(names: List[str], out: str, limit: int = 100000) -> List[str]:
    Path(out).mkdir(exist_ok=True)
    pids = {
        pid: org
        for org in names
        if (
            not Path(f'{out}/{RecUtils.normalize_species(org)}.fasta').exists() and
            (pid := Uniprot.get_proteome_id_by_organism(org))
        )
    }

    proteomes_files = {
        org: str(prot_file)
        for org in names
        if (prot_file := Path(f'{out}/{RecUtils.normalize_species(org)}.fasta')).exists()
    }

    if not pids and not proteomes_files:
        raise Exception('No proteome IDs loaded')

    logging.info(f'Translated organisms names to proteomes IDs: {len(pids) + len(proteomes_files)}/{len(names)}')
    for i, (pid, org) in enumerate(pids.items()):
        if (
            len(proteomes_files) < limit and
            org not in proteomes_files and
            (prot_file := Uniprot.download_proteome(pid, org, out))
        ):
            proteomes_files[org] = prot_file

    logging.info(f'Downloaded proteomes for: {len(proteomes_files)}/{len(names)}')
    return list(proteomes_files.values())


def download_proteomes_by_family(family: str, out: str, limit: int = 100000) -> List[str]:
    ids_file = Uniprot.download_proteomes_ids(family, f'{out}/_ids.tsv')
    Path(out).mkdir(exist_ok=True)
    proteomes_files = {}
    with open(ids_file) as ifp:
        reader = iter(ifp)
        header = next(reader)
        for i, entry in enumerate(reader):
            pid, org, *_ = entry.split('\t')
            if (
                len(proteomes_files) < limit and
                org not in proteomes_files and
                (prot_file := Uniprot.download_proteome(pid, org, out))
            ):
                proteomes_files[org] = prot_file

    logging.info(f'Downloaded proteomes for: {len(proteomes_files)}/{i}')
    Path(ids_file).unlink(missing_ok=True)  # remove file after
    return list(proteomes_files.values())


def download_proteomes(mode: str, input: str, out: str, limit: int = 100000) -> List[str]:
    if mode == 'family':
        return download_proteomes_by_family(input, out, limit)
    elif mode == 'file':
        with open(input) as f:
            organisms = json.load(f)
        return download_proteomes_by_names(organisms, out, limit)
    else:
        raise Exception(f'Wrong mode: mode = {mode}, input = {input}')


def filter_fastas(fastas: List[str], min_seqs: int = 0, max_seqs: int = 100000) -> List[str]:
    if min_seqs == 0 and max_seqs == -1:
        return fastas
    filtered_fastas = [
        file
        for file in fastas
        if max_seqs > RecUtils.count_records(file) >= min_seqs
    ]
    logging.info(f'Filtered fastas with min = {min_seqs}, max = {max_seqs}: {len(filtered_fastas)}/{len(fastas)}')
    return filtered_fastas


def map_recs_to_species(fastas: List[str], out: str) -> (Dict[str, str], Dict[str, str]):
    if Path(out).exists():
        with open(out) as f:
            maps = json.load(f)
            return maps['recs'], maps['orgs']

    # map unique IDs 'A', 'B', ... 'aa', 'ab' to organisms names
    org_ids = RecUtils.generate_ids()
    orgs_map = {
        Path(file).name[:-len('.fasta')]: next(org_ids)
        for file in fastas
    }
    rev_orgs_map = {v: k for k, v in orgs_map.items()}

    recs_map = {}
    for file in fastas:
        seqs = RecUtils.read_records(file)
        org_name = Path(file).name[:-len('.fasta')]
        seqs = {
            seq.id: orgs_map[org_name]
            for seq in seqs
        }
        recs_map.update(seqs)
        logging.info(f'Mapped records ({len(seqs)}) IDs for: {org_name}')

    logging.info(f'Mapped records ({len(recs_map)}) to species ({len(fastas)})')
    with open(out, 'w') as f:
        json.dump({'recs': recs_map, 'orgs': rev_orgs_map}, f, indent=4)
    return recs_map, rev_orgs_map


def merge_fastas(fastas: List[str], out: str) -> str:
    if Path(out).exists():
        return out
    recs = [
        RecUtils.read_records(file)
        for file in fastas
    ]
    merged = list(itertools.chain.from_iterable(recs))
    RecUtils.save_fasta_records(merged, out)
    logging.info(f'Saved all records ({len(merged)}) to file: {out}')
    return out


def clustering(merged_fasta: str,
               out: str,
               min_len: int,
               duplications: bool,
               recs_map: Dict[str, str]) -> Dict[str, list]:
    def rename_fasta_record(seq: SeqRecord, name: str):
        seq.description = ''
        seq.id = name
        return seq

    def get_clusters(f: str) -> Dict[str, List[SeqRecord]]:
        recs = iter(RecUtils.read_records(f))
        # order is important!
        unfiltered_clusters = defaultdict(list)
        cluster_name = next(recs).id
        for rec in recs:
            if len(rec.seq) == 0:  # cluster sequence (id only)
                cluster_name = rec.id
            else:  # real sequence after cluster sequence
                rename_fasta_record(rec, recs_map[rec.id])
                unfiltered_clusters[cluster_name].append(rec)

        logging.info(f'Loaded clusters: {len(unfiltered_clusters)}')

        return unfiltered_clusters

    def filter_clusters(cls: Dict[str, List[SeqRecord]], lim: int, dup: bool) -> Dict[str, list]:
        def filter_corr(cls_recs):
            if dup:
                return cls_recs
            # remove duplicates, one-to-one correspondence
            ids = set()
            corr_cls_recs = []
            for cls_rec in cls_recs:
                if cls_rec.id in ids:
                    continue
                corr_cls_recs.append(cls_rec)
                ids.add(cls_rec.id)
            return corr_cls_recs

        unfiltered_records_cnt = sum(len(fc) for fc in cls.values())
        filtered_clusters = {
            cluster_name: f_cluster_recs
            for cluster_name, cluster_recs in cls.items()
            if len(f_cluster_recs := filter_corr(cluster_recs)) >= lim
        }
        filtered_records_cnt = sum(len(fc) for fc in filtered_clusters.values())
        logging.info(
            f'Filtered clusters with duplication = {dup}, min_len = {lim}: '
            f'clusters {len(filtered_clusters)}/{len(cls)}, records {filtered_records_cnt}/{unfiltered_records_cnt}')
        return filtered_clusters

    clusters_file = Tools.mmseqs2(merged_fasta, out)
    clusters = get_clusters(clusters_file)
    clusters = filter_clusters(clusters, min_len, duplications)

    logging.info(f'Clustered records - unique, filtered clusters: {len(clusters)}')
    return clusters


def make_genes_families(clusters: Dict[str, list], out: str) -> List[str]:
    Path(out).mkdir(exist_ok=True)

    families = []
    for cls_name, cls_recs in clusters.items():
        if not Path(family_filename := f'{out}/{cls_name}.fasta').exists():
            RecUtils.save_fasta_records(cls_recs, family_filename)
        families.append(family_filename)

    logging.info(f'Created protein families from clusters: {len(families)}/{len(clusters)}')
    return families


def align_families(families: List[str], out: str) -> List[str]:
    Path(out).mkdir(exist_ok=True)

    def get_output_filename(fasta: str, output: str) -> str:
        return f'{output}/{Path(fasta).name}'

    def align_fasta_file(fasta_in: str, fasta_out: str):
        if Path(fasta_out).exists():
            return fasta_out
        cline = MuscleCommandline(input=fasta_in, out=fasta_out)
        try:
            subprocess.run(str(cline).split(), check=True)
            return fasta_out
        except subprocess.CalledProcessError:
            logging.error(f'Could not align fasta file: {fasta_in}')
            return ''

    aligned = Parallel(n_jobs=4)(delayed(align_fasta_file)(
        fasta, get_output_filename(fasta, out)
    ) for fasta in families)

    aligned = [fasta_file for fasta_file in aligned if fasta_file]
    logging.info(f'Aligned families: {len(aligned)}/{len(families)}')
    return aligned


def build_trees(aligned_fastas: List[str], out: str) -> (List[str], List[str]):
    Path(out).mkdir(exist_ok=True)
    Path(nj_trees_dir := f'{out}/nj-trees').mkdir(exist_ok=True)
    Path(ml_trees_dir := f'{out}/ml-trees').mkdir(exist_ok=True)
    Path(mp_trees_dir := f'{out}/mp-trees').mkdir(exist_ok=True)

    nj_cons = f'{out}/nj_consensus_tree.nwk'
    ml_cons = f'{out}/ml_consensus_tree.nwk'
    mp_cons = f'{out}/mp_consensus_tree.nwk'
    nj_super = f'{out}/nj_super_tree.nwk'
    ml_super = f'{out}/ml_super_tree.nwk'
    mp_super = f'{out}/mp_super_tree.nwk'

    def unroot_tree(tree_file: str) -> str:
        try:
            cline = f'ete3 mod --unroot -t {tree_file}'
            proc_out = subprocess.run(cline.split(), check=True, capture_output=True)
            proc_out = proc_out.stdout.decode()
            if not proc_out:
                raise Exception(f'invalid oputput')
            # overwrite tree with unrooted version
            with open(tree_file, 'w') as f:
                f.write(proc_out)
            return tree_file
        except subprocess.CalledProcessError as e:
            logging.error(f'Unrooting failed for tree: {tree_file}, error = {str(e)}')
            return ''

    def move_ninja_trees(nj_trees: List[str]):
        Path(nj_trees_dir).mkdir(exist_ok=True)
        for nj_tree in nj_trees:
            family = Path(nj_tree).name
            Path(nj_tree).replace(f'{nj_trees_dir}/{family}.nwk')

    def move_raxml_trees(raxml_trees: List[Tuple[str, str, str]]):
        Path(ml_trees_dir).mkdir(exist_ok=True)
        Path(mp_trees_dir).mkdir(exist_ok=True)
        for ml_tree, mp_tree, family in raxml_trees:
            Path(ml_tree).replace(f'{ml_trees_dir}/{family}.nwk')
            Path(mp_tree).replace(f'{mp_trees_dir}/{family}.nwk')

    def make_ninja_trees():
        Path(f'{out}/ninja').mkdir(exist_ok=True, parents=True)
        ninja_trees = Parallel(n_jobs=4)(
            delayed(Tools.make_ninja_tree)
            (a_fasta, f'{out}/ninja')
            for a_fasta in aligned_fastas
        )
        ninja_trees = [tree for tree in ninja_trees if tree]
        unrooted_ninja_trees = [
            unrooted_tree
            for tree in ninja_trees
            if (unrooted_tree := unroot_tree(tree))
        ]
        logging.info(f'Unrooted ninja NJ trees: {len(unrooted_ninja_trees)}/{len(ninja_trees)}')
        move_ninja_trees(unrooted_ninja_trees)
        logging.info(f'Built unrooted NJ trees using ninja (store at {nj_trees_dir}): {len(ninja_trees)}')

    def make_raxml_trees():
        Path(f'{out}/raxml').mkdir(exist_ok=True, parents=True)
        raxml_trees = Parallel(n_jobs=4)(
            delayed(Tools.make_RAxML_trees)
            (a_fasta, f'{out}/raxml')
            for a_fasta in aligned_fastas
        )
        raxml_trees = [tree for tree in raxml_trees if tree]
        move_raxml_trees(raxml_trees)
        logging.info(f'Built unrooted ML and MP trees using RAxML (store at {ml_trees_dir}, {mp_trees_dir}): {len(raxml_trees)}')

    def make_consensus_trees(o_nj: str, o_ml: str, o_mp: str):
        Tools.make_phylo_consensus_tree(nj_trees_dir, o_nj)
        Tools.make_phylo_consensus_tree(ml_trees_dir, o_ml)
        Tools.make_phylo_consensus_tree(mp_trees_dir, o_mp)

    def make_super_trees(o_nj: str, o_ml: str, o_mp: str):
        Tools.make_clann_super_tree(nj_trees_dir, o_nj)
        Tools.make_clann_super_tree(ml_trees_dir, o_ml)
        Tools.make_clann_super_tree(mp_trees_dir, o_mp)

    if not list(Path(nj_trees_dir).glob('*.nwk')):
        make_ninja_trees()
    if not list(Path(ml_trees_dir).glob('*.nwk')) and not list(Path(mp_trees_dir).glob('*.nwk')):
        make_raxml_trees()

    make_consensus_trees(nj_cons, ml_cons, mp_cons)
    make_super_trees(nj_super, ml_super, mp_super)

    return [nj_cons, ml_cons, mp_cons], [nj_super, ml_super, mp_super]


def retrieve_species_names(trees_files: List[str], orgs_map: Dict[str, str], rm_zero_lengths: bool = False):
    def get_tree_only(tree: str) -> str:
        tree_only = tree.split(';')[0]
        tree_only = f'{tree_only};'
        return tree_only

    def prune_trees(trees: List[str]):
        for tree_f in trees:
            try:
                with open(tree_f) as f:
                    pruned_tree = get_tree_only(f.read())
                with open(tree_f, 'w') as f:
                    f.write(pruned_tree)
            except Exception as e:
                logging.error(f'Could not prune tree from score: {tree_f}, error = {str(e)}')

    prune_trees(trees_files)
    successfully_retrieved = []
    for tree_file in trees_files:
        tree_file_ret = f'{tree_file[:-len(".nwk")]}_species.nwk'
        ret = RecUtils.retrieve_species_names(tree_file, orgs_map, tree_file_ret, rm_zero_lengths)
        if ret:
            successfully_retrieved.append(ret)
    logging.info(f'Retrieved species names for: {len(successfully_retrieved)}/{len(trees_files)}')


def set_logger(log_file: str):
    logging.root.handlers = []
    # noinspection PyArgumentList
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )


def pipeline(input_args):
    Path(input_args.output).mkdir(exist_ok=True)

    set_logger(input_args.log)
    prots = download_proteomes(input_args.mode, input_args.input, input_args.fastas_dir, input_args.num)
    prots = filter_fastas(prots, min_seqs=input_args.filter_min, max_seqs=input_args.filter_max)
    recs_map, orgs_map = map_recs_to_species(prots, f'{input_args.output}/_recsmap.json')
    all_prots = merge_fastas(prots, f'{input_args.output}/_merged.fasta')
    clusters = clustering(
        all_prots,
        f'{input_args.output}/mmseqs2',
        min_len=input_args.cluster_min,
        duplications=input_args.duplications,
        recs_map=recs_map
    )
    families = make_genes_families(clusters, f'{input_args.output}/clusters')
    aligned_families = align_families(families, f'{input_args.output}/align')
    consensus_trees, super_trees = build_trees(aligned_families, f'{input_args.output}/trees')
    retrieve_species_names(consensus_trees, orgs_map)
    retrieve_species_names(super_trees, orgs_map, rm_zero_lengths=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Phylogenetic pipeline to infer a species/genome tree from a set of genomes')
    # parser.add_argument('file', type=str,
    #                     help='file in .json format with list of species to be inferred')
    parser.add_argument('mode', type=str, choices=['family', 'file'],
                        help='pipeline mode')
    parser.add_argument('input', type=str,
                        help='family name or .json file with species names list which will be inferred')
    parser.add_argument('-n', '--num', type=int, default=100000,
                        help='limit downloading species to specific number')
    parser.add_argument('--cluster-min', type=int, default=4,
                        help='filter cluster proteomes minimum, by default: 4')
    parser.add_argument('--filter-min', type=int, default=0,
                        help='filter proteomes minimum')
    parser.add_argument('--filter-max', type=int, default=100000,
                        help='filter proteomes maximum')
    parser.add_argument('--fastas-dir', type=str, default='fastas',
                        help='directory name with fasta files, by default: "fastas/"')
    parser.add_argument('-l', '--log', type=str, default='info.log',
                        help='logger file')
    parser.add_argument('-d', '--duplications', action='store_true', default=False,
                        help='allow duplications (paralogs)')
    parser.add_argument('-o', '--output', type=str,
                        help='output directory, by default: name of family if "family" mode, otherwise "results"')
    args = parser.parse_args()

    if not args.output:
        if args.mode == 'family':
            args.output = args.input
        else:
            args.output = 'results'

    args.log = f'{args.output}/{args.log}'

    pipeline(args)