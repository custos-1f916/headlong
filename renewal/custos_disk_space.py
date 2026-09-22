"""Cheap root-filesystem observations; no model calls or automatic deletion."""
import os


def sample(now):
    stat = os.statvfs('/')
    used = (stat.f_blocks - stat.f_bfree) * stat.f_frsize
    available = max(0, stat.f_bavail) * stat.f_frsize
    capacity = used + available
    if capacity <= 0:
        raise ValueError('root filesystem capacity unavailable')
    return {'at': now, 'path': '/', 'used_bytes': used,
            'available_bytes': available, 'total_bytes': stat.f_blocks * stat.f_frsize,
            'used_percent': 100 * used / capacity}


def observe(observer):
    """Alert at 80/90/95%, once per rising level; re-arm below 75%."""
    reading = sample(observer.now)
    observer.store.put('disk-space:latest', reading)
    old = observer.store.get('disk-space:alert', {'sequence': 0, 'peak': 0})
    percent = reading['used_percent']
    level = next((x for x in (95, 90, 80) if percent >= x), 0)
    recovery = old['peak'] > 0 and percent < 75
    if not recovery and level <= old['peak']:
        return
    sequence = old['sequence'] + 1
    gib = 1024 ** 3
    status = 'recovered below 75%' if recovery else f'crossed {level}% usage'
    content = (f'Custos root disk {status}: {percent:.1f}% used, '
               f'{reading["used_bytes"] / gib:.2f} GiB used and '
               f'{reading["available_bytes"] / gib:.2f} GiB available. '
               'Usage follows df: used / (used + available), accounting for reserved blocks. ')
    if not recovery:
        content += ('Check disk usage and review owned disposable test scratch and caches. '
                    'Preserve memories, trajectories, receipts, source work and rollback releases; '
                    'ask the operator if more capacity is needed. This observation deletes nothing.')
    if observer.emit('disk-space:' + str(sequence), content, authority='agent',
                     disk_space=reading, threshold=0 if recovery else level):
        observer.store.put('disk-space:alert', {'sequence': sequence,
                                              'peak': 0 if recovery else level})
