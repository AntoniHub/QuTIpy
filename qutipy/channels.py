#               This file is part of the QuTIpy package.
#                https://github.com/sumeetkhatri/QuTIpy
#
#                   Copyright (c) 2023 Sumeet Khatri.
#                       --.- ..- - .. .--. -.--
#
#
# SPDX-License-Identifier: AGPL-3.0
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

import itertools

import cvxpy as cvx
import numpy as np
from numpy.linalg import inv, matrix_power
from scipy.linalg import eig, sqrtm

from qutipy.general_functions import (
    Tr,
    dag,
    eye,
    ket,
    partial_trace,
    syspermute,
    tensor,
)
from qutipy.linalg import generate_linear_op_basis, gram_schmidt, vec
from qutipy.misc import cvxpy_to_numpy, numpy_to_cvxpy
from qutipy.pauli import (
    generate_nQubit_Pauli,
    generate_nQubit_Pauli_X,
    generate_nQubit_Pauli_Z,
)
from qutipy.states import max_ent, random_density_matrix, random_state_vector
from qutipy.weyl import discrete_Weyl, discrete_Weyl_Z

##################################################################################
########################## Channel representations ###############################
##################################################################################


def choi_representation(K, dA):
    """
    Calculates the Choi representation of the map with Kraus operators K.
    dA is the dimension of the input space of the channel.

    The Choi represenatation is defined with the channel acting on the second
    half of the maximally entangled vector.
    """

    Gamma = max_ent(dA, normalized=False)

    return apply_channel(K, Gamma, [2], [dA, dA])


def natural_representation(K):
    """
    Calculates the natural representation of the channel (in the standard basis)
    given by the Kraus operators in K. In terms of the Kraus operators, the natural
    representation of the channel in the standard basis is given by

    N=sum_i K_i ⊗ conj(K_i),

    where the sum is over the Kraus operators K_i in K.
    """

    return np.sum([tensor(k, np.conjugate(k)) for k in K], 0)


def choi_to_kraus(P, dA, dB):
    """
    Takes a Choi representation P of a CP map and returns its Kraus representation.

    The Choi representation is defined with the channel acting on the second half of
    the maximally entangled vector.
    """

    D, U = eig(P)

    U_cols = U.shape[1]

    # Need to check if the matrix U generated by eig is unitary (up to
    # numerical precision)
    check1 = np.allclose(eye(dA * dB), U @ dag(U))
    check2 = np.allclose(eye(dA * dB), dag(U) @ U)

    if check1 and check2:
        U = np.array(U)

    # If U is not unitary, use Gram-Schmidt to make it unitary (i.e., make the
    # columns of U orthonormal)
    else:
        C = gram_schmidt([U[:, i] for i in range(U_cols)], dA * dB)
        U = np.sum([tensor(dag(ket(U_cols, i)), C[i]) for i in range(U_cols)], 0)
        # print(U)
    K = []

    for i in range(U_cols):
        Col = U[:, i]
        K_tmp = np.array(np.sqrt(D[i]) * Col.reshape([dA, dB]))
        K.append(K_tmp.transpose())

    return K


def choi_to_natural(C_AB, dA, dB):
    """
    Takes the Choi representation of a superoperator and outputs its natural representation.

    The Choi represenatation Q of the channel acts as:

        vec(N(rho))=Q*vec(rho),

    where N is the channel in question. It can be obtained from the Choi representation
    with a simple reshuffling of indices.
    """

    C_AB = np.array(C_AB)

    return np.array(
        np.reshape(C_AB, [dA, dB, dA, dB])
        .transpose((0, 2, 1, 3))
        .reshape([dA * dA, dB * dB])
    ).T


def choi_to_stinespring(C_AB, dA, dB):
    """
    Takes the Choi representation C_AB of a CP map and outputs its
    Stinespring representation.
    """

    C_AB_purif = vec(sqrtm(C_AB))
    gamma = max_ent(dA, normalized=False, as_matrix=False)

    return tensor(dag(gamma), eye(dB * (dA * dB))) @ tensor(eye(dA), C_AB_purif)


def generate_channel_isometry(K, dA, dB):
    """
    Generates an isometric extension of the
    channel specified by the Kraus operators K. dA is the dimension of the
    input space of the channel, and dB is the dimension of the output space
    of the channel. If dA=dB, then the function also outputs a unitary
    extension of the channel given by a particular construction.
    """

    dimE = len(K)

    V = np.sum([tensor(K[i], ket(dimE, i)) for i in range(dimE)], 0)

    if dA == dB:
        # In this case, the unitary we generate has dimensions dA*dimE x
        # dA*dimE
        U = tensor(V, dag(ket(dimE, 0)))
        states = [V @ ket(dA, i) for i in range(dA)]
        for i in range(dA * dimE - dA):
            states.append(random_state_vector(dA * dimE))

        states_new = gram_schmidt(states, dA * dimE)

        count = dA
        for i in range(dA):
            for j in range(1, dimE):
                U = U + tensor(states_new[count], dag(ket(dA, i)), dag(ket(dimE, j)))
                count += 1

        return V, np.array(U)
    else:
        return V


def transfer_matrix(K, dA, dB, basis="W", as_dict=False):
    """
    For the channel N with input dimension dA, output dimension dB,
    and Kraus operators in K, this function
    generates the coefficients c_{i,j} such that

        c_{i,j}=Tr[B_i^{\dagger}N(B_j)],

    where B_i and B_j are the elements of the chosen basis. This is
    essentially the natural representation, but in a different basis,
    so it is sometimes called a "process matrix" or "transfer matrix".

    Choices for the basis are:

        - basis='W': the discrete-Weyl basis
        - basis='Wtensor': the basis of tensor products of single-qudit
            discrete-Weyl operators. Valid when dA=d^n and dB=d^m.
        - basis='SU': the SU(d) basis
        - basis='SUtensor': the basis of tensor products of single-qudit
            discrete-Weyl operators. Valid when dA=d^n and dB=d^m.
        - basis='pauli': the basis of tensor products of single-qubit
            Pauli operators. Valid when dA=2^n and dB=2^m.
        - basis='standard': this reverts to the natural_representation
            function.

    """

    if basis == "standard":
        return natural_representation(K)
    else:
        if as_dict:
            c = {}
        else:
            c = np.zeros((dB**2, dA**2), dtype=complex)

        Binput = generate_linear_op_basis(dA, basis=basis)
        Boutput = generate_linear_op_basis(dB, basis=basis)

        for j in range(len(Binput)):
            for i in range(len(Boutput)):
                if as_dict:
                    c[(i, j)] = (1 / dB) * Tr(
                        dag(Boutput[i]) @ apply_channel(K, Binput[j])
                    )
                else:
                    c[i, j] = (1 / dB) * Tr(
                        dag(Boutput[i]) @ apply_channel(K, Binput[j])
                    )

        return c


##################################################################################
########################### General functions ####################################
##################################################################################


def apply_channel(K, rho, sys=None, dim=None, adjoint=False):
    """
    Applies the channel with Kraus operators in K to the state rho on
    systems specified by the list sys. The dimensions of the subsystems of
    rho are given by dim.

    If adjoint is True, then this function applies the adjoint of the given
    channel.
    """

    if isinstance(rho, cvx.Variable):
        rho = cvxpy_to_numpy(rho)
        rho_out = apply_channel(K, rho, sys, dim, adjoint)
        return numpy_to_cvxpy(rho_out)

    if adjoint:
        K_tmp = K
        K = []
        K = [dag(K_tmp[i]) for i in range(len(K_tmp))]

    if sys is None:  # Applying the channel to the full state.
        return np.sum([K[i] @ rho @ dag(K[i]) for i in range(len(K))], 0)
    else:  # Applying the channel to subsystems
        A = []
        n = len(
            dim
        )  # [2, 2, _2, _2] Total number of systems corresponding to the state rho
        k = len(
            sys
        )  # [ 3, 4 ] # Total number of systems on which the channel is being applied
        indices = itertools.product(
            range(len(K)), repeat=k
        )  # All possible tuples of the indices of the Kraus operators of the channel
        for index in indices:
            l = 0
            X = 1
            for i in range(n):
                if i + 1 in sys:
                    X = tensor(X, K[index[l]])
                    l += 1
                else:
                    X = tensor(X, eye(dim[i]))
            A.append(X)

        return np.sum([A[i] @ rho @ dag(A[i]) for i in range(len(A))], 0)


def compose_channels(C):
    """
    Takes a composition of channels. The variable C should be a list of lists,
    with each list consisting of the Kraus operators of the channels to be composed.

    If C=[K1,K2,...,Kn], then this function returns the composition such that
    the channel corresponding to K1 is applied first, then K2, etc.
    """

    d = C[0][0].shape[0]

    lengths = []
    for c in C:
        lengths.append(len(c))

    combs = list(itertools.product(*[range(length) for length in lengths]))

    K_n = []

    for comb in combs:
        # tmp=1
        tmp = eye(d)
        for i in range(len(comb)):
            tmp = C[i][comb[i]] @ tmp
        K_n.append(tmp)

    return K_n


def tensor_channels(C):
    """
    Takes the tensor product of the channels in C.

    C is a set of sets of Kraus operators.
    """

    lengths = []
    for c in C:
        lengths.append(len(c))

    combs = list(itertools.product(*[range(length) for length in lengths]))

    K_n = []

    for comb in combs:
        tmp = 1
        for i in range(len(comb)):
            tmp = tensor(tmp, C[i][comb[i]])
        K_n.append(tmp)

    return K_n


def n_channel_uses(K, n):
    """
    Given the Kraus operators K of a channel, this function generates the
    Kraus operators corresponding to the n-fold tensor power of the channel.
    dA is the dimension of the input space, and dA the dimension of the
    output space.
    """

    r = len(K)  # Number of Kraus operators

    combs = list(itertools.product(*[range(r)] * n))

    K_n = []

    for comb in combs:
        # print comb
        tmp = 1
        for i in range(n):
            tmp = tensor(tmp, K[comb[i]])
        K_n.append(tmp)

    return K_n


def channel_scalar_multiply(K, x):
    """
    Multiplies the channel with Kraus operators in K by the scalar x.
    This means that each Kraus operator is multiplied by sqrt(x)!
    """

    K_new = []

    for i in range(len(K)):
        K_new.append(np.sqrt(x) * K[i])

    return K_new


def diamond_norm(J, dA, dB, display=False):
    """
    Computes the diamond norm of a superoperator with Choi representation J.
    dA is the dimension of the input space of the channel, and dB is the
    dimension of the output space.

    The form of the SDP used comes from Theorem 3.1 of:

        'Simpler semidefinite programs for completely bounded norms',
            Chicago Journal of Theoretical Computer Science 2013,
            by John Watrous
    """

    """
    The Choi representation J in the above paper is defined using a different
    convention:
        J=(N⊗ I)(|Phi^+><Phi^+|).
    In other words, the channel N acts on the first half of the maximally-
    entangled state, while the convention used throughout this code stack
    is
        J=(I⊗ N)(|Phi^+><Phi^+|).
    We thus use syspermute to convert to the form used in the aforementioned
    paper.
    """

    J = syspermute(J, [2, 1], [dA, dB])

    X = cvx.Variable((dA * dB, dA * dB), complex=True)
    rho0 = cvx.Variable((dA, dA), PSD=True)
    rho1 = cvx.Variable((dA, dA), PSD=True)

    M = cvx.bmat([[cvx.kron(eye(dB), rho0), X], [X.H, cvx.kron(eye(dB), rho1)]])

    c = []
    c += [M >> 0, cvx.trace(rho0) == 1, cvx.trace(rho1) == 1]

    obj = cvx.Maximize(
        (1 / 2) * cvx.real(cvx.trace(dag(J) @ X))
        + (1 / 2) * cvx.real(cvx.trace(J @ X.H))
    )

    prob = cvx.Problem(obj, constraints=c)

    prob.solve(verbose=display, eps=1e-7)

    return prob.value


##################################################################################
############################# Random channels ####################################
##################################################################################


def random_CP_map(dA, dB, TP=False, unital=False, return_as="choi"):
    """
    Generates a random completely-positive (CP) map with input dimension dA
    and output dimension dB.

    We generate the CP map via a randomly-chosen bipartite quantum state that
    represents the Choi state of the map.

    The return_as optional argument can be either:
        - 'choi' (default)
        - 'kraus'
        - 'natural'
        - 'stinespring'
    """

    C_AB = random_density_matrix(dA * dB)

    if not TP and not unital:
        C_AB = C_AB

    elif TP and not unital:
        C_A = partial_trace(C_AB, [2], [dA, dB])
        C_A_inv_sq = tensor(inv(sqrtm(C_A)), eye(dB))
        C_AB = C_A_inv_sq @ C_AB @ C_A_inv_sq

    elif not TP and unital:
        C_B = partial_trace(C_AB, [1], [dA, dB])
        C_B_inv_sq = tensor(eye(dA), inv(sqrtm(C_B)))
        C_AB = C_B_inv_sq @ C_AB @ C_B_inv_sq

    elif TP and unital:
        # Note here that we need dA=dB!
        if dA != dB:
            return "Input and output dimensions must match for a TP and unital CP map!"
        else:
            None  ##### TO DO
    else:
        C_AB = C_AB

    if return_as == "choi":
        return C_AB
    elif return_as == "kraus":
        return choi_to_kraus(C_AB, dA, dB)
    elif return_as == "natural":
        return choi_to_natural(C_AB, dA, dB)
    elif return_as == "stinespring":
        return choi_to_stinespring(C_AB, dA, dB)
    else:
        print("Output format not recognized -- returning Choi representation...\n")
        return C_AB


def random_quantum_channel(dA, dB, unital=False, return_as="choi"):
    """
    Generates a random quantum channel -- a completely-positive and trace-preserving
    superoperator -- with input dimension dA and output dimension dB.
    """

    return random_CP_map(dA, dB, TP=True, unital=unital, return_as=return_as)


def random_POVM(d, num_elem, via_choi=True):
    """
    Generates a random POVM in d dimensions with num_elem elements.

    If via_choi=True, then we generate the POVM by generating a random
    quantum channel, and then taking the diagonal blocks of its Choi
    representation (on the output system) as the POVM elements.

    If via_choi=False, then we generate the POVM by generating num_elem
    random quantum states, and then doing the 'pretty-good measurement'
    construction.
    """

    M = []

    if via_choi:
        C = random_quantum_channel(d, num_elem)
        for i in range(num_elem):
            Mi = (
                tensor(eye(d), dag(ket(num_elem, i)))
                @ C
                @ tensor(eye(d), ket(num_elem, i))
            )
            M.append(Mi)

    else:
        S = [random_density_matrix(d) for i in range(num_elem)]
        R = np.sum(S, 0)
        R_inv_sq = inv(sqrtm(R))
        for i in range(num_elem):
            Mi = R_inv_sq @ S[i] @ R_inv_sq

    return M


##################################################################################
############################## Pauli channels ####################################
##################################################################################


def Pauli_channel(px, py, pz):
    """
    Generates the Kraus operators, an isometric extension, and a unitary
    extension of the one-qubit Pauli channel specified by the parameters px, py, pz.
    """

    pI = 1 - px - py - pz

    Sx = np.array([[0, 1], [1, 0]])
    Sy = np.array([[0, -1j], [1j, 0]])
    Sz = np.array([[1, 0], [0, -1]])

    K = [np.sqrt(pI) * eye(2), np.sqrt(px) * Sx, np.sqrt(py) * Sy, np.sqrt(pz) * Sz]

    V, U = generate_channel_isometry(K, 2, 2)

    return K, V, U


def depolarizing_channel(p):
    """
    For 0<=p<=1, this returns the one-qubit Pauli channel given by px=py=pz=p/3.
    """

    return Pauli_channel(p / 3.0, p / 3.0, p / 3.0)


def depolarizing_channel_n_uses(p, n, rho, m):
    """
    Generates the output state corresponding to the depolarizing channel
    applied to each one of n systems in the joint state rho. p is the
    depolarizing probability as defined in the function "depolarizing_channel"
    above.

    If rho contains m>n systems, then the first m-n systems are left alone.
    """

    dims = 2 * np.ones(m).astype(int)

    rho_out = np.zeros((2**m, 2**m))

    for k in range(n + 1):
        indices = list(itertools.combinations(range(1, n + 1), k))

        # print k,indices

        for index in indices:
            index = list(index)

            index = np.array(index) + (m - n)
            index = list(index.astype(int))

            index_diff = np.setdiff1d(range(1, m + 1), index)

            perm_arrange = np.append(index, index_diff).astype(int)
            perm_rearrange = np.zeros(m)

            for i in range(m):
                perm_rearrange[i] = np.argwhere(perm_arrange == i + 1)[0][0] + 1

            perm_rearrange = perm_rearrange.astype(int)

            mix = matrix_power(eye(2**k) / 2, k)

            rho_part = partial_trace(rho, index, dims)

            rho_out = rho_out + (4 * p / 3.0) ** k * (1 - (4 * p / 3.0)) ** (
                n - k
            ) * syspermute(tensor(mix, rho_part), perm_rearrange, dims)

    return rho_out


def bit_flip_channel(p):
    """
    Generates the channel rho -> (1-p)*rho+p*X*rho*X.
    """

    return Pauli_channel(p, 0, 0)


def dephasing_channel(p, d=2):
    """
    Generates the channel rho -> (1-p)*rho+p*Z*rho*Z. (In the case d=2.)

    For d>=2, we let p be a list of d probabilities, and we use the discrete Weyl-Z
    operators to define the channel.

    For p=1/d, we get the completely dephasing channel.
    """

    if d == 2:
        return Pauli_channel(0, 0, p)
    else:
        K = [np.sqrt(p[k]) * matrix_power(discrete_Weyl_Z(d), k) for k in range(d)]
        return K


def completely_dephasing_channel(d):
    """
    Generates the completely dephasing channel in d dimensions. This channel
    eliminates the off-diagonal elements (in the standard basis) of the input operator.
    """

    if d == 2:
        p = 1 / 2
        return dephasing_channel(p, d=d)[0]
    else:
        p = (1 / d) * np.ones(d)
        return dephasing_channel(p, d=d)


def BB84_channel(Q):
    """
    Generates the channel corresponding to the BB84 protocol with
    equal X and Z errors, given by the QBER Q. The definition of this
    channel can be found in:

        "Additive extensions of a quantum channel", by
            Graeme Smith and John Smolin. (arXiv:0712.2471)

    """

    return Pauli_channel(Q - Q**2, Q**2, Q - Q**2)


def Pauli_channel_nQubit(n, p, alt_repr=False):
    """
    Generates the Kraus operators, an isometric extension, and a unitary
    extension of the n-qubit Pauli channel specified by the 2^(2*n) parameters in
    p, which must be probabilities in order for the map to be a channel. (i.e.,
    they must be non-negative and sum to one.)

    If alt_repr=True, then the channel is of the form

    P(rho)=\sum_{a,b} p_{a,b} X^aZ^b(rho)Z^bX^a

    where a and b are n-bit strings
    (using the n-qubit X and Z operators as generated by the functions
    generate_nQubit_Pauli_X and generate_nQubit_Pauli_Z).
    """

    K = []

    if not alt_repr:
        S = list(itertools.product(*[range(0, 4)] * n))
        for i in range(2 ** (2 * n)):
            K.append(np.sqrt(p[i]) * generate_nQubit_Pauli(list(S[i])))

        V, U = generate_channel_isometry(K, 2**n, 2**n)

        return K, V, U

    else:  # alt_repr==True
        S = list(itertools.product(*[range(0, 2)] * n))
        count = 0
        for a in S:
            a = list(a)
            for b in S:
                b = list(b)
                K.append(
                    np.sqrt(p[count])
                    * generate_nQubit_Pauli_X(a)
                    @ generate_nQubit_Pauli_Z(b)
                )
                count += 1

        V, U = generate_channel_isometry(K, 2**n, 2**n)

        return K, V, U


def depolarizing_channel_nQubits(n, p):
    """
    For 0<=p<=1, this returns the n-qubit Pauli channel given by
    p[0]=1-p, p[i]=p/(2^(2*n)-1) for all i>=1.
    """

    p = [1 - p] + [p / (2 ** (2 * n) - 1) for i in range(2 ** (2 * n) - 1)]

    return Pauli_channel_nQubit(n, p, alt_repr=True)


# def channel_nQubit_coeffs(K, n, as_dict=False):
#   CONVERT THIS TO A FUNCTION FOR THE EIGENVALUES OF A PAULI CHANNEL
#    """
#    Generates the coefficients c_{a,b} such that

#        P(X^aZ^b)=c_{a,b}X^aZ^b,

#    for the n-qubit channel P with the Kraus operators in K.
#    """

#    if as_dict:
#        c = {}
#    else:
#        c = []

#    S = list(itertools.product(*[range(0, 2)] * n))
# print(S)

#    for a in S:
#        for b in S:
#            Xa = generate_nQubit_Pauli_X(list(a))
#            Zb = generate_nQubit_Pauli_Z(list(b))
#            if as_dict:
#                c[(a, b)] = (1 / 2**n) * Tr(dag(Xa @ Zb) @ apply_channel(K, Xa @ Zb))
#            else:
#                c.append((1 / 2**n) * Tr(dag(Xa @ Zb) @ apply_channel(K, Xa @ Zb)))

#    return c


def Pauli_channel_qudit(d, p):
    """
    Generates the Kraus operators, an isometric extension, and a unitary
    extension of the d-dimensional Pauli channel defined via the discrete
    Weyl operators. The variable p is a list of d^2 probabilities that sum to one.
    """

    K = []
    i = 0

    for z in range(d):
        for x in range(d):
            K.append(np.sqrt(p[i]) * discrete_Weyl(d, z, x))
            i += 1

    V, U = generate_channel_isometry(K, d, d)

    return K, V, U


##################################################################################
############################## Other channels ####################################
##################################################################################


def phase_damping_channel(p):
    """
    Generates the phase damping channel.
    """

    K1 = np.array([[1, 0], [0, np.sqrt(p)]])
    K2 = np.array([[0, 0], [0, np.sqrt(1 - p)]])

    return [K1, K2]


def amplitude_damping_channel(gamma):
    """
    Generates the amplitude damping channel.
    """

    A1 = np.array([[1, 0], [0, np.sqrt(1 - gamma)]])
    A2 = np.array([[0, np.sqrt(gamma)], [0, 0]])

    return [A1, A2]


def generalized_amplitude_damping_channel(gamma, N):
    """
    Generates the generalized amplitude damping channel.
    """

    if N == 0:
        return amplitude_damping_channel(gamma)
    elif N == 1:
        A1 = np.array([[np.sqrt(1 - gamma), 0], [0, 1]])
        A2 = np.array([[0, 0], [np.sqrt(gamma), 0]])
        return [A1, A2]
    else:
        A1 = np.sqrt(1 - N) * np.array([[1, 0], [0, np.sqrt(1 - gamma)]])
        A2 = np.sqrt(1 - N) * np.array([[0, np.sqrt(gamma)], [0, 0]])
        A3 = np.sqrt(N) * np.array([[np.sqrt(1 - gamma), 0], [0, 1]])
        A4 = np.sqrt(N) * np.array([[0, 0], [np.sqrt(gamma), 0]])

        return [A1, A2, A3, A4]
