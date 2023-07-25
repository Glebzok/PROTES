import jax
import jax.numpy as jnp
import optax
from time import perf_counter as tpc


def protes_general(f, n, m=None, k=100, k_top=10, k_gd=1, lr=5.E-2, r=5, seed=0,
                   is_max=False, log=False, log_ind=False, info={}, P=None,
                   with_info_i_opt_list=False, with_info_full=False):
    time = tpc()
    info.update({'n': n, 'm_max': m, 'm': 0, 'k': k, 'k_top': k_top,
        'k_gd': k_gd, 'lr': lr, 'r': r, 'seed': seed, 'is_max': is_max,
        'is_rand': P is None, 't': 0, 'i_opt': None, 'y_opt': None,
        'm_opt_list': [], 'i_opt_list': [], 'y_opt_list': []})
    if with_info_full:
        info.update({
            'P_list': [], 'I_list': [], 'y_list': []})

    rng = jax.random.PRNGKey(seed)

    if P is None:
        rng, key = jax.random.split(rng)
        P = _generate_initial(n, r, key)

    optim = optax.adam(lr)
    state = optim.init(P)

    sample = jax.jit(jax.vmap(_sample, (None, 0)))
    likelihood = jax.jit(jax.vmap(_likelihood, (None, 0)))

    @jax.jit
    def loss(P_cur, I_cur):
        l = likelihood(P_cur, I_cur)
        return jnp.mean(-l)

    loss_grad = jax.grad(loss)

    @jax.jit
    def optimize(state, P_cur, I_cur):
        grads = loss_grad(P_cur, I_cur)
        updates, state = optim.update(grads, state)
        P_cur = jax.tree_util.tree_map(lambda u, p: p + u, updates, P_cur)
        return state, P_cur

    while True:
        rng, key = jax.random.split(rng)
        I = sample(P, jax.random.split(key, k))

        y = f(I)
        if y is None:
            break

        y = jnp.array(y)
        info['m'] += y.shape[0]

        is_new = _check(P, I, y, info, with_info_i_opt_list, with_info_full)

        if info['m_max'] and info['m'] >= info['m_max']:
            break

        ind = jnp.argsort(y, kind='stable')
        ind = (ind[::-1] if is_max else ind)[:k_top]

        for _ in range(k_gd):
            state, P = optimize(state, P, I[ind, :])

        info['t'] = tpc() - time
        _log(info, log, log_ind, is_new)

    info['t'] = tpc() - time
    _log(info, log, log_ind, is_new, is_end=True)

    return info['i_opt'], info['y_opt']


def _check(P, I, y, info, with_info_i_opt_list, with_info_full):
    """Check the current batch of function values and save the improvement."""
    ind_opt = jnp.argmax(y) if info['is_max'] else jnp.argmin(y)

    i_opt_curr = I[ind_opt, :]
    y_opt_curr = y[ind_opt]

    is_new = info['y_opt'] is None
    is_new = is_new or info['is_max'] and info['y_opt'] < y_opt_curr
    is_new = is_new or not info['is_max'] and info['y_opt'] > y_opt_curr

    if is_new:
        info['i_opt'] = i_opt_curr
        info['y_opt'] = y_opt_curr

    if is_new or with_info_full:
        info['m_opt_list'].append(info['m'])
        info['y_opt_list'].append(info['y_opt'])

        if with_info_i_opt_list or with_info_full:
            info['i_opt_list'].append(info['i_opt'].copy())

    if with_info_full:
        info['P_list'].append([G.copy() for G in P])
        info['I_list'].append(I.copy())
        info['y_list'].append(y.copy())

    return is_new


def _generate_initial(n, r, key):
    """Build initial random TT-tensor for probability."""
    d = len(n)
    r = [1] + [r]*(d-1) + [1]
    keys = jax.random.split(key, d)

    Y = []
    for j in range(d):
        Y.append(jax.random.uniform(keys[j], (r[j], n[j], r[j+1])))

    return Y


def _interface_matrices(Y):
    """Compute the "interface matrices" for the TT-tensor."""
    d = len(Y)
    Z = [[]] * (d+1)
    Z[0] = jnp.ones(1)
    Z[d] = jnp.ones(1)
    for j in range(d-1, 0, -1):
        Z[j] = jnp.sum(Y[j], axis=1) @ Z[j+1]
        Z[j] /= jnp.linalg.norm(Z[j])
    return Z


def _likelihood(Y, I):
    """Compute the likelihood in a multi-index I for TT-tensor."""
    d = len(Y)

    Z = _interface_matrices(Y)

    G = jnp.einsum('riq,q->i', Y[0], Z[1])
    G = jnp.abs(G)
    G /= G.sum()

    y = [G[I[0]]]

    Z[0] = Y[0][0, I[0], :]

    for j in range(1, d):
        G = jnp.einsum('r,riq,q->i', Z[j-1], Y[j], Z[j+1])
        G = jnp.abs(G)
        G /= jnp.sum(G)

        y.append(G[I[j]])

        Z[j] = Z[j-1] @ Y[j][:, I[j], :]
        Z[j] /= jnp.linalg.norm(Z[j])

    return jnp.sum(jnp.log(jnp.array(y)))


def _log(info, log=False, log_ind=False, is_new=False, is_end=False):
    """Print current optimization result to output."""
    if not log or (not is_new and not is_end):
        return

    text = f'protes > '
    text += f'm {info["m"]:-7.1e} | '
    text += f't {info["t"]:-9.3e} | '
    text += f'y {info["y_opt"]:-11.4e}'

    if log_ind:
        text += f' | i {" ".join([str(i) for i in info["i_opt"]])}'

    if is_end:
        text += ' <<< DONE'

    print(text)


def _sample(Y, key):
    """Generate sample according to given probability TT-tensor."""
    d = len(Y)
    keys = jax.random.split(key, d)
    I = jnp.zeros(d, dtype=jnp.int32)

    Z = _interface_matrices(Y)

    G = jnp.einsum('riq,q->i', Y[0], Z[1])
    G = jnp.abs(G)
    G /= G.sum()

    i = jax.random.choice(keys[0], jnp.arange(Y[0].shape[1]), p=G)
    I = I.at[0].set(i)

    Z[0] = Y[0][0, i, :]

    for j in range(1, d):
        G = jnp.einsum('r,riq,q->i', Z[j-1], Y[j], Z[j+1])
        G = jnp.abs(G)
        G /= jnp.sum(G)

        i = jax.random.choice(keys[j], jnp.arange(Y[j].shape[1]), p=G)
        I = I.at[j].set(i)

        Z[j] = Z[j-1] @ Y[j][:, i, :]
        Z[j] /= jnp.linalg.norm(Z[j])

    return I
