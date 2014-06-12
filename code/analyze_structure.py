# -*- coding: utf-8 -*-
"""
Created on Fri Mar  8 10:29:13 2013

@author: niklas
"""
import os
import time
import subprocess
from math import sqrt
import pandas as pd
import helper_functions as hf

from Bio.PDB.PDBParser import PDBParser
from Bio.PDB import PDBIO

import logging
import errors
import pdb_template

from collections import OrderedDict, deque


def get_interactions_between_chains(model, pdb_chain_1, pdb_chain_2, r_cutoff=5):
    """
    Calculate interactions between residues in pdb_chain_1 and pdb_chain_2. An
    interaction is defines as a pair of residues where at least one pair of atom
    is closer than r_cutoff. The default value for r_cutoff is 5 Angstroms.
    """
    # model = structure[0]

    def calculate_distance(atom1, atom2):
        a = atom1.coord
        b = atom2.coord
        assert(len(a) == 3 and len(b) == 3)
        return sqrt(sum( (a - b)**2 for a, b in zip(a, b) ))

    # Extract the chains of interest from the model
    chain_1 = None
    chain_2 = None
    for child in model.get_list():
        if child.id == pdb_chain_1:
            chain_1 = child
        if child.id == pdb_chain_2:
            chain_2 = child
    if chain_1 is None or chain_2 is None:
        raise Exception('Chains %s and %s were not found in the model' % (pdb_chain_1, pdb_chain_2))

    interactions_between_chains = OrderedDict()
    for idx, residue_1 in enumerate(chain_1):
        if residue_1.resname in pdb_template.amino_acids and residue_1.id[0] == ' ':
            resnum_1 = str(residue_1.id[1]) + residue_1.id[2].strip()
            resaa_1 = pdb_template.convert_aa(residue_1.get_resname())
            interacting_resids = []
            for residue_2 in chain_2:
                resnum_2 = str(residue_2.id[1]) + residue_2.id[2].strip()
                resaa_2 = pdb_template.convert_aa(residue_2.get_resname())
                r_min = None
                if residue_2.resname in pdb_template.amino_acids and residue_2.id[0] == ' ':
                    for atom_1 in residue_1:
                        for atom_2 in residue_2:
                            r = calculate_distance(atom_1, atom_2)
                            if r < r_cutoff:
                                if r_min and r < r_min:
                                    r_min = r
                                elif not r_min:
                                    r_min = r
                if r_min:
                    interacting_resids.append((resnum_2, resaa_2, r_min,))
            if interacting_resids:
                interactions_between_chains[(resnum_1, resaa_1)] = interacting_resids
    return interactions_between_chains


class AnalyzeStructure(object):
    """
    Runs the program pops to calculate the interface size of the complexes
    This is done by calculating the surface of the complex and the seperated parts.
    The interface is then given by the substracting
    """

    def __init__(self, data_path, working_path, pdb_file, chains, domain_defs, log):

        self.data_path = data_path # modeller_path, foldx_path
        self.working_path = working_path # analyze_structure path with all the binaries
        self.pdb_file = pdb_file
        self.chain_ids = chains
        self.domain_defs = domain_defs
        self.log = log
        self.structure = self.__split_pdb_into_chains()


    def __split_pdb_into_chains(self):

        parser = PDBParser(QUIET=True) # set QUIET to False to output warnings like incomplete chains etc.
        io = PDBIO()
        self.log.debug('Saving parsed pdbs into the following working path:')
        self.log.debug(self.data_path + self.pdb_file)

        # Save all chains together with correct chain letters
        self.log.debug('Saving all chains together')
        structure = parser.get_structure('ID', self.data_path + self.pdb_file)
        if len(structure) > 1:
            # Delete all models except for the first (otherwise get errors with naccess)
            del structure[1:]
        model = structure[0]
        children = model.get_list()
#        for child in children:
#            self.log.debug('child id before:' + child.id)
#            if child.id not in self.chain_ids:
#            self.log.debug('child id after:' + child.id)
        if len(children) == 1 and (children[0].id == '' or children[0].id == ' ' or children[0].id == '0'):
            children[0].id = self.chain_ids[0]
        if len(children) == 2 and children[0].id == '0' and children[1].id == '0':
            children[0].id = 'A'
            children[1].id = 'B'
        io.set_structure(structure)
        outFile = self.working_path +  self.pdb_file
        io.save(outFile)

        if len(self.chain_ids) > 1:
            # Save a structure with only the chains of interest
            self.log.debug('Saving only the chains of interest: %s' % ','.join(self.chain_ids))
            structure = parser.get_structure('ID', self.data_path + self.pdb_file)
            model = structure[0]
            for child in model.get_list():
                self.log.debug('child id:' + child.id)
                if child.id not in self.chain_ids:
                    self.log.debug('detaching chain')
                    model.detach_child(child.id)
            io.set_structure(structure)
            outFile = self.working_path + ''.join(self.chain_ids) + '.pdb'
            io.save(outFile)

        # Save a structure for each chain
        for chain_id in self.chain_ids:
            self.log.debug('Saving chain %s separately' % chain_id)
            # save chain, i.e. part one of the complex:
            structure = parser.get_structure('ID', self.data_path + self.pdb_file)
            model = structure[0]
            for child in model.get_list():
                self.log.debug('child id:' + child.id)
                if child.id != chain_id:
                    self.log.debug('detaching chain %s' % child.id)
                    model.detach_child(child.id)
            io.set_structure(structure)
            outFile = self.working_path + chain_id + '.pdb'
            io.save(outFile)

        # The main class structure is the one that only has the chains of interest
        structure = parser.get_structure('ID', self.working_path + ''.join(self.chain_ids) + '.pdb')
        return structure

    ###########################################################################

    def get_sasa(self, program_to_use='naccess'):

        if program_to_use == 'naccess':
            run_sasa_atom = self._run_naccess_atom
        elif program_to_use == 'pops':
            run_sasa_atom = self._run_pops_atom
        else:
            raise Exception('Unknown program specified!')

        sasa_score_splitchains = {}
        for chain_id in self.chain_ids:
            sasa_score_splitchains.update(run_sasa_atom(chain_id + '.pdb'))
        sasa_score_allchains = run_sasa_atom(''.join(self.chain_ids) + '.pdb')
        return [sasa_score_splitchains, sasa_score_allchains]


    def get_seasa(self):

        seasa_by_chain_together, seasa_by_residue_together = self._run_msms(''.join(self.chain_ids) + '.pdb')

        if len(self.chain_ids) > 1:
            seasa_by_chain_separately = []
            seasa_by_residue_separately = []
            for chain_id in self.chain_ids:
                seasa_by_chain, seasa_by_residue = self._run_msms(chain_id + '.pdb')
                seasa_by_chain_separately.append(seasa_by_chain)
                seasa_by_residue_separately.append(seasa_by_residue)
            seasa_by_chain_separately = pd.concat(seasa_by_chain_separately, ignore_index=True)
            seasa_by_residue_separately = pd.concat(seasa_by_residue_separately, ignore_index=True)
            return [seasa_by_chain_together, seasa_by_chain_separately, seasa_by_residue_together, seasa_by_residue_separately]
        else:
            return [seasa_by_chain_together, seasa_by_chain_together, seasa_by_residue_together, seasa_by_residue_together]

    def _run_msms(self, filename):
        """ In the future, could add an option to measure residue depth
        using Bio.PDB.ResidueDepth().residue_depth()...
        """
        base_filename = filename[:filename.rfind('.')]

        # Get a list of standard accessibilities for a ALA-X-ALA tripeptide
        # (obtained from naccess)
        with open(self.working_path + 'standard.data', 'r') as fh:
            standard_data = fh.readlines()
        standard_sasa_all = [ [l.strip() for l in line.split()] for line in standard_data ][1:]
        standard_sasa = {}
        deque((standard_sasa.update({x[3]: float(x[4])}) for x in standard_sasa_all), maxlen=0)

#        # Get a dictionary to map from atom serial numbers to pdb chain and residue name
#        atom_to_chain = {}
#        atom_to_res_name = {}
#        atom_to_res_num = {}
#        atom_to_atom_id = {}
#        parser = PDBParser(QUIET=True)
#        structure = parser.get_structure('ID', self.data_path + self.pdb_file)
#        model = structure[0]
#        for chain in model:
#            for residue in chain:
#                if residue.resname in pdb_template.amino_acids \
#                and residue.id[0] == ' ':
#                    for atom in residue:
#                            atom_to_chain[atom.serial_number] = chain.id.strip()
#                            atom_to_res_name[atom.serial_number] = residue.resname.strip()
#                            atom_to_res_num[atom.serial_number] = str(residue.id[1]) + residue.id[2].strip()
#                            atom_to_atom_id[atom.serial_number] = atom.id

        # Convert pdb to xyz coordiates
        assert(os.path.isfile(self.working_path + filename))
        system_command = './pdb_to_xyzrn {0}.pdb'.format(self.working_path + base_filename)
        self.log.debug('msms system command 1: %s' % system_command)
        child_process = hf.run_subprocess_locally(self.working_path, system_command)
        result, error_message = child_process.communicate()
        return_code = child_process.returncode
        if return_code != 0:
            self.log.debug('msms result 1:')
            self.log.debug(result)
            self.log.debug('msms error message 1:')
            self.log.debug(error_message)
            self.log.debug('naccess rc 1:')
            self.log.debug(child_process.returncode)
            raise errors.MSMSError(error_message)
        else:
            with open(self.working_path + base_filename + '.xyzrn', 'w') as ofh:
                ofh.writelines(result)

        # Calculate SASA and SESA (excluded)
        system_command = (
            './msms.x86_64Linux2.2.6.1 '
            '-probe_radius 1.4 '
            '-surface ases '
            '-if {0}.xyzrn '
            '-af {0}.area'.format(self.working_path + base_filename))
        self.log.debug('msms system command 2: %s' % system_command)
        child_process = hf.run_subprocess_locally(self.working_path, system_command)
        result, error_message = child_process.communicate()
        return_code = child_process.returncode
        if return_code != 0:
            self.log.debug('msms result 2:')
            self.log.debug(result)
            self.log.debug('msms error message 2:')
            self.log.debug(error_message)
            self.log.debug('naccess rc 2:')
            self.log.debug(child_process.returncode)
            raise errors.MSMSError(error_message)

        # Read and parse the output
        with open(self.working_path + base_filename + '.area', 'r') as fh:
            file_data = fh.readlines()
        file_data = [ [l.strip() for l in line.split()] for line in file_data]
        del file_data[0]
        file_data = [[int(x[0]), float(x[1]), float(x[2]), x[3].split('_')[0].strip(), x[3].split('_')[1].strip(), x[3].split('_')[2], x[3].split('_')[3]] for x in file_data]
        seasa_df = pd.DataFrame(data=file_data, columns=['atom_num', 'abs_sesa', 'abs_sasa', 'atom_id', 'res_name', 'res_num', 'pdb_chain'])
        seasa_df['atom_num'] = seasa_df['atom_num'].apply(lambda x: x + 1)
        seasa_df['rel_sasa'] = [x[0] / standard_sasa.get(x[1], x[0]) * 100 for x in zip(seasa_df['abs_sasa'], seasa_df['res_name'])]
#        seasa_df['chain'] = seasa_df['atom_num'].apply(lambda x: atom_to_chain.get(x, None))
#        seasa_df['res_name'] = seasa_df['atom_num'].apply(lambda x: atom_to_res_name.get(x, None))
#        seasa_df['res_num'] = seasa_df['atom_num'].apply(lambda x: atom_to_res_num.get(x, None))
#        seasa_df['atom_id'] = seasa_df['atom_num'].apply(lambda x: atom_to_atom_id.get(x, None))
#        seasa_df.dropna(inplace=True)

#        incorrect_assignments = \
#            [x for x in zip(seasa_df['res_name'], seasa_df['res_name_msms']) if x[0]!=x[1]] + \
#            [x for x in zip(seasa_df['atom_id'], seasa_df['atom_id_msms']) if x[0]!=x[1]]
#        if len(incorrect_assignments) > 5:
#            self.log.error('Could not correctly assign msms output to chains!')
#            self.log.error(incorrect_assignments)
#            raise errors.MSMSError('Could not correctly assign msms output to chains: ' + str(incorrect_assignments))
#        del seasa_df['atom_id_msms']
#        del seasa_df['res_name_msms']
#        del seasa_df['res_num_msms']

        seasa_gp_by_chain = seasa_df.groupby(['pdb_chain'])
        seasa_gp_by_residue = seasa_df.groupby(['pdb_chain', 'res_name', 'res_num'])
        seasa_by_chain = seasa_gp_by_chain.sum().reset_index()
        seasa_by_residue = seasa_gp_by_residue.sum().reset_index()

        return seasa_by_chain, seasa_by_residue


    def _run_naccess_atom(self, filename):
        # run naccess
        system_command = ('./naccess ' + filename)
        self.log.debug('naccess system command: %s' % system_command)
        assert(os.path.isfile(self.working_path + filename))
        child_process = hf.run_subprocess_locally(self.working_path, system_command)
        result, error_message = child_process.communicate()
        return_code = child_process.returncode
        self.log.debug('naccess result: {}'.format(result))
        self.log.debug('naccess error: {}'.format(error_message))
        self.log.debug('naccess rc: {}'.format(return_code))
        # Collect results
        sasa_scores = {}
        with open(self.working_path + filename.split('.')[0] + '.rsa') as fh:
            for line in fh:
                row = line.split()
                if row[0] != 'RES':
                    continue
                try:
                    (line_id, res, chain, num, all_abs, all_rel,
                    sidechain_abs, sidechain_rel, mainchain_abs, mainchain_rel,
                    nonpolar_abs, nonpolar_rel, polar_abs, polar_rel) = row
                except ValueError as e:
                    print e
                    print line
                    print row
                sasa_scores.setdefault(chain, []).append(sidechain_rel) # percent sasa on sidechain
        return sasa_scores


    def _run_pops_atom(self, chain_id):
        # Use pops to calculate sasa score for the given chain
        termination, rc, e = self.__run_pops_atom(chain_id)
        if termination != 'Clean termination':
            self.log.error('Pops error for pdb: %s, chains: %s: ' % (self.pdb_file, ' '.join(self.chain_ids),) )
            self.log.error(e)
            raise errors.PopsError(e, self.data_path + self.pdb_file, self.chain_ids)
        else:
            self.log.warning('Pops error for pdb: %s, chains: %s: ' % (self.pdb_file, ' '.join(self.chain_ids),) )
            self.log.warning(e)

        # Read the sasa scores from a text file
        sasa_scores = self.__read_pops_atom(chain_id)
        return sasa_scores


    def __run_pops_atom(self, chain_id):
        system_command = ('./pops --noHeaderOut --noTotalOut --atomOut --pdb {0}.pdb --popsOut {0}.out'.format(chain_id))
        child_process = hf.run_subprocess_locally(self.working_path, system_command)
        result, error_message = child_process.communicate()
        return_code = child_process.returncode
        # The returncode can be non zero even if pops calculated the surface
        # area. In that case it is indicated by "clean termination" written
        # to the output. Hence this check:
        # if output[-1] == 'Clean termination' the run should be OK
        self.log.debug('result: %s' % result)
        output = [ line for line in result.split('\n') if line != '' ]
        return output[-1], return_code, error_message


    def __read_pops_atom(self, chain_id):
        """
        Read pops sasa results atom by atom, ignoring all main chain atoms except for Ca
        """
        # The new way
        ignore = ['N', 'C', 'O']
        per_residue_sasa_scores = []
        current_residue_number = None
        with open(self.working_path + chain_id + '.out', 'r') as fh:
            for line in fh:
                row = line.split()
                if len(row) != 11:
                    continue
                atom_number, atom_name, residue_name, chain, residue_number, sasa, __, __, __, __, sa = line.split()
                atom_number, residue_number, sasa, sa = int(atom_number), int(residue_number), float(sasa), float(sa)
                if atom_name in ignore:
                    continue
                if current_residue_number != residue_number:
                    if current_residue_number:
                        per_residue_sasa_scores.append(total_sasa/total_sa)
                    current_residue_number = residue_number
                    total_sasa = 0
                    total_sa = 0
                total_sasa += sasa
                total_sa += sa
            per_residue_sasa_scores.append(total_sasa/total_sa)
        return per_residue_sasa_scores


###############################################################################

    def get_dssp(self):
        """
        """
        n_tries = 0
        return_code = -1
        while return_code != 0 and n_tries < 5:
            if n_tries > 0:
                self.log.debug('Waiting for 1 minute before trying again...')
                time.sleep(60)
            system_command = ('./dssp -i ' + ''.join(self.chain_ids) + '.pdb' + ' -o ' + 'dssp_results.txt')
            self.log.debug('dssp system command: %s' % system_command)
            child_process = hf.run_subprocess_locally(self.working_path, system_command)
            result, error_message = child_process.communicate()
            return_code = child_process.returncode
            self.log.debug('dssp return code: %i' % return_code)
            self.log.debug('dssp result: %s' % result)
            self.log.debug('dssp error: %s' % error_message)
            n_tries += 1
        if return_code != 0:
            if 'boost::thread_resource_error' in error_message:
                raise errors.ResourceError(error_message)
        # collect results
        dssp_ss = {}
        dssp_acc = {}
        start = False
        with open(self.working_path + 'dssp_results.txt') as fh:
            for l in fh:
                row = l.split()
                if not row or len(row) < 2:
                    continue
                if row[1] == "RESIDUE":
                    # Start parsing from here
                    start = True
                    continue
                if not start:
                    continue
                if l[9] == ' ':
                    # Skip -- missing residue
                    continue
                resseq, icode, chainid, aa, ss = int(l[5:10]), l[10], l[11], l[13], l[16]
                if ss == ' ':
                    ss = '-'
                try:
                    acc = int(l[34:38])
#                    phi = float(l[103:109])
#                    psi = float(l[109:115])
                except ValueError, exc:
                    # DSSP output breaks its own format when there are >9999
                    # residues, since only 4 digits are allocated to the seq num
                    # field.  See 3kic chain T res 321, 1vsy chain T res 6077.
                    # Here, look for whitespace to figure out the number of extra
                    # digits, and shift parsing the rest of the line by that amount.
                    if l[34] != ' ':
                        shift = l[34:].find(' ')
                        acc = int((l[34+shift:38+shift]))
#                        phi = float(l[103+shift:109+shift])
#                        psi = float(l[109+shift:115+shift])
                    else:
                        raise ValueError(exc)
                dssp_ss.setdefault(chainid, []).append(ss) # percent sasa on sidechain
                dssp_acc.setdefault(chainid, []).append(acc)
        for key in dssp_ss.keys():
            dssp_ss[key] = ''.join(dssp_ss[key])
        return dssp_ss, dssp_acc


###############################################################################

    def get_interchain_distances(self, pdb_chain=None, pdb_mutation=None, cutoff=5):
        """
        """

        model = self.structure[0]
        chains = [ chain for chain in model ]

        shortest_interchain_distances = {}
        for chain_1 in chains:
            if pdb_chain and chain_1.id != pdb_chain:
                continue # skip chains that we are not interested in
            shortest_interchain_distances[chain_1.id] = list()
            for idx, residue_1 in enumerate(chain_1):
                if residue_1.resname in pdb_template.amino_acids and residue_1.id[0] == ' ':
                    if pdb_mutation:
                        if (str(residue_1.id[1]) + residue_1.id[2].strip()) != pdb_mutation[1:-1]:
                            continue # skip all residues that we are not interested in
                        if pdb_template.convert_aa(residue_1.resname) != pdb_mutation[0] \
                        and pdb_template.convert_aa(residue_1.resname) != pdb_mutation[-1]:
                            self.log.debug(pdb_mutation)
                            self.log.debug(pdb_template.convert_aa(residue_1.resname))
                            self.log.debug(residue_1.id)
                            raise Exception
                    min_r = None
                    for chain_2 in [c for c in chains if c != chain_1]:
                        for residue_2 in chain_2:
                            if residue_1.resname not in pdb_template.amino_acids \
                            or residue_2.resname not in pdb_template.amino_acids:
                                continue

                            for atom_1 in residue_1:
                                for atom_2 in residue_2:
                                    r = self.calculate_distance(atom_1, atom_2)
                                    if not min_r or min_r > r:
                                        min_r = r
                    shortest_interchain_distances[chain_1.id].append(min_r)

        self.log.debug('interacting_aa_keys:')
        self.log.debug(shortest_interchain_distances.keys())

        return shortest_interchain_distances


    def calculate_distance(self, atom1, atom2):
        """
        returns the distance of two points in three dimensional space
        input: atom instance of biopython: class 'Bio.PDB.Atom.Atom
        return: type 'float'
        """
        a = atom1.coord
        b = atom2.coord
        assert(len(a) == 3 and len(b) == 3)
        return sqrt(sum( (a - b)**2 for a, b in zip(a, b) ))


###############################################################################

    def get_interface_area(self):

        termination, rc, e = self.__run_pops_area(self.working_path + ''.join(self.chain_ids) + '.pdb')
        if rc != 0:
            if termination != 'Clean termination':
                self.log.error('Pops error for pdb: %s:' % self.pdb_file)
                self.log.error(e)
                return '0', '0', '0'
        result = self.__read_pops_area(self.working_path + ''.join(self.chain_ids) + '.out')

        # Distinguish the surface area by hydrophobic, hydrophilic, and total
        for item in result:
            if item[0] == 'hydrophobic:':
                hydrophobic = float(item[1])
            elif item[0] == 'hydrophilic:':
                hydrophilic = float(item[1])
            elif item[0] == 'total:':
                total = float(item[1])
        sasa_complex = hydrophobic, hydrophilic, total

        # calculate SASA for chain, i.e. part one of the complex:
        termination, rc, e = self.__run_pops_area(self.working_path + self.chain_ids[0] + '.pdb')
        if rc != 0:
            if termination != 'Clean termination':
                self.log.error('Error in pops for pdb: %s:' % self.pdb_file)
                self.log.error(e)
                return '0', '0', '0'
        result = self.__read_pops_area(self.working_path + self.chain_ids[0] + '.out')

        # Distinguish the surface area by hydrophobic, hydrophilic, and total
        for item in result:
            if item[0] == 'hydrophobic:':
                hydrophobic = float(item[1])
            elif item[0] == 'hydrophilic:':
                hydrophilic = float(item[1])
            elif item[0] == 'total:':
                total = float(item[1])
        sasa_chain = hydrophobic, hydrophilic, total

        # calculate SASA for oppositeChain, i.e. the second part of the complex:
        termination, rc, e = self.__run_pops_area(self.working_path + self.chain_ids[1] + '.pdb')
        if rc != 0:
            if termination != 'Clean termination':
                self.log.error('Error in pops for pdb: %s:' % self.pdb_file)
                self.log.error(e)
                return '0', '0', '0'
        result = self.__read_pops_area(self.working_path + self.chain_ids[1] + '.out')

        for item in result:
            if item[0] == 'hydrophobic:':
                hydrophobic = float(item[1])
            elif item[0] == 'hydrophilic:':
                hydrophilic = float(item[1])
            elif item[0] == 'total:':
                total = float(item[1])
        sasa_oppositeChain = hydrophobic, hydrophilic, total

        sasa = [ 0, 0, 0 ]
        # hydrophobic
        sasa[0] = (sasa_chain[0] + sasa_oppositeChain[0] - sasa_complex[0]) / 2.0
        # hydrophilic
        sasa[1] = (sasa_chain[1] + sasa_oppositeChain[1] - sasa_complex[1]) / 2.0
        # total
        sasa[2] = (sasa_chain[2] + sasa_oppositeChain[2] - sasa_complex[2]) / 2.0

        return sasa


    def __run_pops_area(self, full_filename):
        system_command = ('./pops --chainOut'
            ' --pdb ' + full_filename +
            ' --popsOut ' + self.working_path + full_filename.split('/')[-1].replace('pdb', 'out'))
        child_process = hf.run_subprocess_locally(self.working_path, system_command)
        result, error_message = child_process.communicate()
        return_code = child_process.returncode
        # The returncode can be non zero even if pops calculated the surface
        # area. In that case it is indicated by "clean termination" written
        # to the output. Hence this check:
        # if output[-1] == 'Clean termination' the run should be OK
        output = [ line for line in result.split('\n') if line != '' ]
        self.log.debug(system_command)
#        self.log.debug('pops result: %s' % result) # Prints the entire POPs output
#        self.log.debug('pops error: %s' % e)
        error_message_1 = 'Warning: Atom distance too short! Probably incorrect POPS results!'
        if error_message_1 in error_message:
            self.log.error(error_message_1)
        self.log.debug('pops rc: %s' % return_code)
        return output[-1], return_code, error_message


    def __read_pops_area(self, filename):
        # The old way
        keep = ['hydrophobic:', 'hydrophilic:', 'total:']
        with open(filename, 'r') as pops:
            result = [ x.split(' ') for x in pops.readlines() if x != '' and x.split(' ')[0] in keep ]
        return [ [ x.strip() for x in item if x != '' ] for item in result ]


###############################################################################
# Old code used to deal with single-point mutations
# We now calculate dssp and interface amino acids for the entire model
    def __check_structure(self, pdbCode, chainID, mutation):
        """ checks if the mutation falls into the interface, i.e. is in contact with
        another chain
        'mutation' has to be of the form A_T70H, mutation in chain A, from Tyr at
        position 70 to His
        NOTE: takes the mutation as numbered ins sequence! The conversion is done
        within this function!

        input
        pdbCode     type 'str'
        chainID     type 'str'
        mutation    type 'str'      ; B_Q61L

        return:
        contacts    type 'dict'     ; {'C': False, 'B': True}
                                      key:   chainID                type 'str'
                                      value: contact to chainID     type boolean
        """
        structure = pdb_template.get_pdb(pdbCode, self.pdbPath, self.working_path)
        model = structure[0]

        chains   = [ chain for chain in model]
        chainIDs = [ chain.id for chain in model]

        positions = pdb_template.convert_position_to_resid(model, mutation[0], [int(mutation[3:-1])]) # convert the position numbering
        position = mutation[:3] + str(positions[0]) + mutation[-1]
        contacts = { chainID: False for chainID in chainIDs if not chainID == mutation[0] }

        for i in range(len(chains)):
            if chains[i].id == mutation[0]:
                chain = chains[i]
                # use list expansion to select only the 'opposing chains'
                oppositeChains = [ x for x in chains if x != chains[i] ]

        # If the residues do not match, issue a warning.
        # To obtain a better model one could restrict to templates that have
        # the same amino acid as the uniprot sequence at the position of the mutation.
#        if chain[position].resname != self.convert_aa(fromAA):
#            print 'Residue missmatch while checking the structure!'
#            print 'pdbCode', pdbCode
#            print 'mutation', mutation
#            print chain[position].resname, self.convert_aa(fromAA)
#            print 'position', position


        for oppositeChain in oppositeChains:
            # check each residue
            for residue in oppositeChain:
                # for each residue each atom of the mutated residue has to be checked
                for atom1 in chain[position]: # chain[position] is the residue that should be mutated
                    # and each atom
                    for atom2 in residue:
                        r = self.distance(atom1, atom2)
                        if r <= 5.0:
                            contacts[oppositeChain.id] = True
        return contacts

    ###########################################################################





if __name__ == '__main__':
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    logger.handlers = []
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)

#    pdb_file = 'Q9Y6K1_Q9UBC3.BL00030001.pdb'
#    data_path = '/home/kimlab1/database_data/elaspic/human/Q9Y/6K/Q9Y6K1/PWWP*291-374/PWWP*224-307/Q9UBC3/'
#    working_path = '/tmp/elaspic/NUORFs/analyze_structure/'
#    chain = ['A','B']
#
#    analyze_structure = AnalyzeStructure(data_path, working_path, pdb_file, chain, None, logger)
#    dssp_score = analyze_structure.get_dssp()
#    sasa_score = analyze_structure.get_sasa()
#    interchain_distances = analyze_structure.get_interchain_distances('A_Q10N')
#
#    print dssp_score[0]['A'][10]
#    print sasa_score[0]['A'][10]
#    print interchain_distances['A'][0]

    # Get SASA using pops
    # Get interacting amino acids and interface area
    unique_temp_folder = '/tmp/elaspic/NzBMEx/'
    pdbFile_wt = 'RepairPDB_Q8IWL2_P35247.BL00020001_1.pdb'
    modeller_chains = ['B']
    analyze_structure = AnalyzeStructure(unique_temp_folder + 'FoldX/',
                                         unique_temp_folder + 'analyze_structure/',
                                         pdbFile_wt, modeller_chains, None, logger)

    contact_distance_wt = analyze_structure_wt.get_interchain_distances(chain_mutation_modeller)[modeller_chains[0]][0]

#    interacting_aa = analyze_structure.get_interchain_distances()
#    interacting_aa_1 = interacting_aa[modeller_chains[0]]
#    interacting_aa_1 = ','.join(['%i' % x for x in interacting_aa_1 if x])
#
#    interacting_aa_2 = interacting_aa[modeller_chains[1]]
#    interacting_aa_2 = ','.join(['%i' % x for x in interacting_aa_2 if x])

#    interface_area = analyze_structure.get_interface_area()
#    interface_area_hydrophobic = interface_area[0]
#    interface_area_hydrophilic = interface_area[1]
#    interface_area_total = interface_area[2]

