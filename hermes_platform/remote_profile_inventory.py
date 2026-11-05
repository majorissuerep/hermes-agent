"""Descriptor-pinned native entries for fixed Linux profile transfer effects.

Tar silently omits sockets. A tar digest is therefore never a complete native
entry inventory. This fixed target code refuses unsupported entries, inventories
supported names without following links, and rechecks every destructive effect.
"""

_PROGRAM = r'''
INVENTORY_BYTES = 64 * 1024**2
INVENTORY_ENTRIES = 250000

def native_stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_uid, info.st_nlink)

def native_record(parent, name):
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    kind = stat.S_IFMT(before.st_mode)
    if before.st_uid != os.getuid() or kind not in (stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK):
        raise ValueError("unsupported or foreign native profile entry; every copy retained")
    record = {"kind": kind, "inode": [before.st_dev, before.st_ino],
              "mode": stat.S_IMODE(before.st_mode), "uid": before.st_uid}
    if kind == stat.S_IFREG:
        if before.st_nlink != 1:
            raise ValueError("native profile hard link is not supported; every copy retained")
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as source:
            if native_stamp(os.fstat(source.fileno())) != native_stamp(before):
                raise ValueError("native profile file changed before read; every copy retained")
            digest = hashlib.sha256()
            while block := source.read(1048576):
                digest.update(block)
            if native_stamp(os.fstat(source.fileno())) != native_stamp(before):
                raise ValueError("native profile file changed during read; every copy retained")
        record.update(size=before.st_size, sha256=digest.hexdigest(),
                      mtime_ns=before.st_mtime_ns, ctime_ns=before.st_ctime_ns)
    elif kind == stat.S_IFLNK:
        record.update(sha256=hashlib.sha256(os.fsencode(os.readlink(name, dir_fd=parent))).hexdigest(),
                      mtime_ns=before.st_mtime_ns, ctime_ns=before.st_ctime_ns)
    if native_stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) != native_stamp(before):
        raise ValueError("native profile entry changed during inventory; every copy retained")
    return record

def native_names(descriptor, used=None):
    used = [0, 0] if used is None else used
    names = []
    with os.scandir(descriptor) as listing:
        for entry in listing:
            amount = len(os.fsencode(entry.name))
            if used[1] >= INVENTORY_ENTRIES or used[0] + amount > INVENTORY_BYTES:
                raise ValueError("native profile names exceed their bounded inventory; retained")
            used[0] += amount
            used[1] += 1
            names.append(entry.name)
    return sorted(names)

def native_walk(descriptor, prefix, records, children, used):
    names = native_names(descriptor, used)
    children[prefix] = names
    for name in names:
        relative = prefix + "/" + name if prefix else name
        if len(relative) > 4096 or len(records) >= INVENTORY_ENTRIES:
            raise ValueError("native profile inventory exceeds its entry/path bound; retained")
        record = native_record(descriptor, name)
        used[0] += len(json.dumps({relative: record}, sort_keys=True, separators=(",", ":")))
        if used[0] > INVENTORY_BYTES:
            raise ValueError("native profile inventory exceeds its byte bound; retained")
        records[relative] = record
        if record["kind"] == stat.S_IFDIR:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            try:
                info = os.fstat(child)
                if [info.st_dev, info.st_ino] != record["inode"]:
                    raise ValueError("native profile directory changed; retained")
                native_walk(child, relative, records, children, used)
            finally:
                os.close(child)
    if native_names(descriptor) != names:
        raise ValueError("native profile names changed during inventory; retained")

def native_inventory(descriptor):
    records, children = {}, {}
    native_walk(descriptor, "", records, children, [0, 0])
    return records, children

def native_encoded(descriptor, identity, records):
    info = os.fstat(descriptor)
    if (info.st_dev, info.st_ino) != identity:
        raise ValueError("native profile root changed during inventory; retained")
    encoded = json.dumps({"version": 1, "root": list(identity), "entries": records},
                         sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > INVENTORY_BYTES:
        raise ValueError("native profile inventory exceeds its byte bound; retained")
    return encoded

def native_directory_check(parent, name, child, record, check):
    def anchored():
        check()
        for info in (os.stat(name, dir_fd=parent, follow_symlinks=False), os.fstat(child)):
            if (not stat.S_ISDIR(info.st_mode) or [info.st_dev, info.st_ino] != record["inode"]
                    or info.st_uid != record["uid"] or stat.S_IMODE(info.st_mode) != record["mode"]):
                raise ValueError("native profile ancestor replaced before cleanup; retained")
    return anchored

def native_remove(descriptor, records, children, check, prefix=""):
    check()
    if native_names(descriptor) != children[prefix]:
        raise ValueError("new or missing native profile entry before cleanup; retained")
    for name in children[prefix]:
        relative = prefix + "/" + name if prefix else name
        if name == BINDING and not prefix:
            continue
        check()
        record = native_record(descriptor, name)
        if record != records[relative]:
            raise ValueError("native profile entry changed before cleanup; retained")
        if record["kind"] == stat.S_IFDIR:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            try:
                child_check = native_directory_check(descriptor, name, child, record, check)
                child_check()
                native_remove(child, records, children, child_check, relative)
                child_check()
                if native_record(descriptor, name) != record:
                    raise ValueError("native profile directory replaced during cleanup; retained")
                os.rmdir(name, dir_fd=descriptor)
            finally:
                os.close(child)
        else:
            check()
            if native_record(descriptor, name) != record:
                raise ValueError("native profile entry replaced before unlink; retained")
            os.unlink(name, dir_fd=descriptor)
'''


def program() -> str:
    """The machine-side implementation embedded into the existing fixed helper."""
    return _PROGRAM
