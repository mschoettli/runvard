"""Resolve Compose sources from Docker metadata without guessing or dropping files."""
import json
import os

from modules.compose_utils import yaml


class _Tagged:
    def __init__(self, tag, value):
        self.tag, self.value = tag, value


def _yaml_codecs():
    class Loader(yaml.SafeLoader):
        pass

    class Dumper(yaml.SafeDumper):
        def choose_scalar_style(self):
            if self.event.tag in ('!reset', '!override') and self.event.style == '':
                return ''
            return super().choose_scalar_style()

    def construct(loader, node):
        if isinstance(node, yaml.MappingNode):
            loader.flatten_mapping(node)
            value = loader.construct_mapping(node, deep=True)
        elif isinstance(node, yaml.SequenceNode):
            value = loader.construct_sequence(node, deep=True)
        else:
            scalar_tag = loader.resolve(yaml.ScalarNode, node.value, (node.style is None, False))
            value = loader.construct_object(yaml.ScalarNode(scalar_tag, node.value, style=node.style), deep=True)
        return _Tagged(node.tag, value)

    def represent(dumper, value):
        node = dumper.represent_data(value.value)
        if isinstance(node, yaml.ScalarNode):
            node.style = '"' if isinstance(value.value, str) else ''
        node.tag = value.tag
        return node

    for tag in ('!reset', '!override'):
        Loader.add_constructor(tag, construct)
    Dumper.add_representer(_Tagged, represent)
    return Loader, Dumper


def _preserve_edits(content, document, loader, dumper):
    """Patch existing tokens and append entries without rewriting surrounding YAML."""
    root = yaml.compose(content, Loader=loader)
    edits = []

    def append(node, value, indentation):
        end = node.end_mark.index
        line_start = content.rfind('\n', 0, end) + 1
        position = line_start if not content[line_start:end].strip() else end
        prefix = '' if position == 0 or content[position - 1] == '\n' else '\n'
        rendered = yaml.dump(value, Dumper=dumper, sort_keys=False, allow_unicode=True)
        rendered = ''.join(' ' * indentation + line + '\n' for line in rendered.splitlines())
        edits.append((position, position, prefix + rendered))

    def visit(node, value):
        if isinstance(node, yaml.MappingNode) and isinstance(value, dict):
            keys = [key.value for key, _ in node.value]
            if not set(keys).issubset(value):
                return False
            added = {key: item for key, item in value.items() if key not in keys}
            if added:
                if node.flow_style or not node.value:
                    return False
                append(node, added, node.value[0][0].start_mark.column)
            return all(visit(child, value[key.value]) for key, child in node.value)
        if isinstance(node, yaml.SequenceNode) and isinstance(value, list):
            if len(value) < len(node.value):
                return False
            if len(value) > len(node.value):
                if node.flow_style or not node.value:
                    return False
                append(node, value[len(node.value):], max(0, node.value[0].start_mark.column - 2))
            return all(visit(child, item) for child, item in zip(node.value, value))
        if not isinstance(node, yaml.ScalarNode) or isinstance(value, (dict, list)):
            return False
        original = yaml.load(content[node.start_mark.index:node.end_mark.index], Loader=loader)
        if isinstance(original, _Tagged):
            original = original.value
        if original == value and type(original) is type(value):
            return True
        # Quoted strings also handle multiline text without disturbing indentation.
        if isinstance(value, str) and node.style != "'":
            replacement = json.dumps(value, ensure_ascii=False)
        else:
            replacement = yaml.dump(value, Dumper=dumper, default_flow_style=True,
                                    default_style=node.style if node.style in ("'", '"') else None,
                                    allow_unicode=True).removesuffix('...\n').rstrip('\n')
        if node.tag in ('!reset', '!override'):
            replacement = node.tag + ' ' + replacement
        edits.append((node.start_mark.index, node.end_mark.index, replacement))
        return True

    try:
        if not visit(root, document):
            return None
        patched = content
        for start, end, replacement in sorted(set(edits), reverse=True):
            patched = patched[:start] + replacement + patched[end:]
        # An alias may share a scalar token with another setting. Never change it
        # unintentionally just to retain the original textual representation.
        if convert(content=patched)['document'] == document:
            return patched
    except (TypeError, ValueError, yaml.YAMLError):
        pass
    return None


def convert(content=None, document=None, yaml_tags=None):
    if yaml is None:
        raise ValueError('YAML support is unavailable')
    import copy
    loader, dumper = _yaml_codecs()
    try:
        tags = []

        def unwrap(value, path):
            if isinstance(value, _Tagged):
                tags.append({'path': path, 'tag': value.tag})
                return unwrap(value.value, path)
            if isinstance(value, dict):
                return {key: unwrap(item, path + [key]) for key, item in value.items()}
            if isinstance(value, list):
                return [unwrap(item, path + [index]) for index, item in enumerate(value)]
            return value

        if document is not None:
            data = json.loads(document)
            tags = json.loads(yaml_tags) if yaml_tags is not None else []
        else:
            data = unwrap(yaml.load(content, Loader=loader), [])
        if not isinstance(data, dict) or not data or ('services' in data and not isinstance(data['services'], dict)):
            raise ValueError('Compose configuration must be a mapping')
        # The form must round-trip every field through JSON without type loss.
        json.dumps(data, allow_nan=False)
        if not isinstance(tags, list) or any(not isinstance(item, dict) or item.get('tag') not in ('!reset', '!override')
                                            or not isinstance(item.get('path'), list) for item in tags):
            raise ValueError('Invalid Compose YAML tags')
        output = copy.deepcopy(data)
        for entry in sorted(tags, key=lambda item: len(item['path']), reverse=True):
            path = entry['path']
            if not path:
                output = _Tagged(entry['tag'], output)
                continue
            parent = output
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = _Tagged(entry['tag'], parent[path[-1]])
        result = yaml.dump(output, Dumper=dumper, sort_keys=False, allow_unicode=True)
        if document is not None and content:
            preserved = _preserve_edits(content, data, loader, dumper)
            if preserved is not None:
                result = preserved
        return {'document': data, 'content': result, 'tags': tags}
    except (TypeError, ValueError, KeyError, IndexError, yaml.YAMLError) as exc:
        raise ValueError(f'Invalid Compose configuration: {exc}') from exc


def discover(containers, name):
    standalone = name.startswith('container:')
    target = name.split(':', 1)[1] if standalone else name
    matches = []
    files = []
    directory = ''
    for c in containers:
        labels = (c.attrs.get('Config') or {}).get('Labels') or {}
        if (c.name != target if standalone else labels.get('com.docker.compose.project') != target):
            continue
        matches.append(c)
        wd = labels.get('com.docker.compose.project.working_dir', '')
        directory = directory or wd
        for filename in labels.get('com.docker.compose.project.config_files', '').split(','):
            if not filename:
                continue
            path = os.path.abspath(os.path.join(wd, filename))
            if path not in files:
                files.append(path)
    return matches, files, directory


def runtime_document(containers):
    """Reconstruct the configurable container state when no source file is recorded."""
    services, volumes, networks = {}, {}, {}
    for c in containers:
        attrs = c.attrs
        config = attrs.get('Config') or {}
        host = attrs.get('HostConfig') or {}
        labels = config.get('Labels') or {}
        name = labels.get('com.docker.compose.service') or c.name
        service = {'image': config.get('Image'), 'container_name': c.name}
        for original, field in [('Env', 'environment'), ('Cmd', 'command'),
                                ('Entrypoint', 'entrypoint'), ('User', 'user'),
                                ('WorkingDir', 'working_dir'), ('Hostname', 'hostname'),
                                ('Domainname', 'domainname'), ('StopSignal', 'stop_signal'),
                                ('Tty', 'tty'), ('OpenStdin', 'stdin_open')]:
            if config.get(original) is not None and config[original] != '':
                service[field] = config[original]
        custom_labels = {k: v for k, v in labels.items() if not k.startswith('com.docker.compose.')}
        if custom_labels:
            service['labels'] = custom_labels
        restart = host.get('RestartPolicy') or {}
        if restart.get('Name'):
            service['restart'] = restart['Name'] + (f":{restart['MaximumRetryCount']}" if restart.get('MaximumRetryCount') else '')
        ports = []
        for target, bindings in (host.get('PortBindings') or {}).items():
            for binding in bindings or []:
                ip = binding.get('HostIp', '')
                if ':' in ip:
                    ip = f'[{ip}]'
                prefix = ip + ':' if ip else ''
                published = binding.get('HostPort', '')
                ports.append(f'{prefix}{published}:{target}' if published else target)
        if ports:
            service['ports'] = ports
        if config.get('ExposedPorts'):
            service['expose'] = list(config['ExposedPorts'])
        mounts = []
        for m in attrs.get('Mounts') or []:
            kind = m.get('Type', 'bind')
            mount = {'type': kind, 'target': m['Destination'], 'read_only': not m.get('RW', True)}
            if kind == 'volume':
                source = m.get('Name') or m.get('Source')
                volumes[source] = {'external': True, 'name': source}
                mount['source'] = source
            elif kind == 'bind':
                mount['source'] = m['Source']
                if m.get('Propagation'):
                    mount['bind'] = {'propagation': m['Propagation']}
            mounts.append(mount)
        if mounts:
            service['volumes'] = mounts
        for original, field in [('Privileged', 'privileged'), ('ReadonlyRootfs', 'read_only'),
                                ('CapAdd', 'cap_add'), ('CapDrop', 'cap_drop'), ('Dns', 'dns'),
                                ('DnsSearch', 'dns_search'), ('ExtraHosts', 'extra_hosts'),
                                ('SecurityOpt', 'security_opt'), ('ShmSize', 'shm_size'),
                                ('Memory', 'mem_limit'), ('MemoryReservation', 'mem_reservation'),
                                ('MemorySwap', 'memswap_limit'), ('PidsLimit', 'pids_limit'),
                                ('Init', 'init'), ('Sysctls', 'sysctls'), ('GroupAdd', 'group_add'),
                                ('IpcMode', 'ipc'), ('PidMode', 'pid'),
                                ('Runtime', 'runtime'), ('OomKillDisable', 'oom_kill_disable')]:
            if host.get(original):
                service[field] = host[original]
        if host.get('Tmpfs'):
            service['tmpfs'] = [f'{target}:{options}' if options else target for target, options in host['Tmpfs'].items()]
        if host.get('NanoCpus'):
            service['cpus'] = host['NanoCpus'] / 1_000_000_000
        if host.get('Devices'):
            service['devices'] = [f"{d['PathOnHost']}:{d['PathInContainer']}:{d['CgroupPermissions']}" for d in host['Devices']]
        if host.get('Ulimits'):
            service['ulimits'] = {u['Name']: {'soft': u['Soft'], 'hard': u['Hard']} for u in host['Ulimits']}
        logging = host.get('LogConfig') or {}
        if logging.get('Type'):
            service['logging'] = {'driver': logging['Type'], 'options': logging.get('Config') or {}}
        health = config.get('Healthcheck') or {}
        if health:
            service['healthcheck'] = {}
            for key, value in health.items():
                field = {'StartPeriod': 'start_period', 'StartInterval': 'start_interval'}.get(key, key.lower())
                service['healthcheck'][field] = f'{value}ns' if key in ('Interval', 'Timeout', 'StartPeriod', 'StartInterval') else value
        mode = host.get('NetworkMode')
        if mode in ('host', 'none') or str(mode).startswith('container:'):
            service['network_mode'] = mode
        else:
            attached = attrs.get('NetworkSettings', {}).get('Networks') or {}
            if attached:
                service['networks'] = {}
                for network, settings in attached.items():
                    networks[network] = {'external': True, 'name': network}
                    options = {}
                    if settings.get('Aliases'):
                        options['aliases'] = settings['Aliases']
                    ipam = settings.get('IPAMConfig') or {}
                    for key, field in [('IPv4Address', 'ipv4_address'), ('IPv6Address', 'ipv6_address')]:
                        if ipam.get(key):
                            options[field] = ipam[key]
                    service['networks'][network] = options
        services[name] = service
    document = {'services': services}
    if volumes:
        document['volumes'] = volumes
    if networks:
        document['networks'] = networks
    return document


def write_sources(changes):
    """Stage all files before replacing sources; restore earlier files on failure."""
    import stat
    import tempfile

    staged, originals, committed = {}, {}, []

    def stage(path, content, metadata):
        fd, temporary = tempfile.mkstemp(prefix='.runvard-compose-', dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, 'wb') as f:
                if metadata:
                    os.fchmod(f.fileno(), stat.S_IMODE(metadata.st_mode))
                    if os.geteuid() == 0:
                        os.fchown(f.fileno(), metadata.st_uid, metadata.st_gid)
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            return temporary
        except Exception:
            os.unlink(temporary)
            raise

    try:
        for filename, content in changes.items():
            path = os.path.realpath(filename)
            if path in originals:
                raise ValueError('Compose source paths overlap')
            try:
                with open(path, 'rb') as f:
                    previous, metadata = f.read(), os.fstat(f.fileno())
            except FileNotFoundError:
                previous, metadata = None, None
            originals[path] = previous, metadata
            updated = content.encode('utf-8') if content is not None else None
            if previous == updated:
                continue
            staged[path] = stage(path, updated, metadata) if updated is not None else None
        for path, temporary in staged.items():
            if temporary is None:
                os.unlink(path)
            else:
                os.replace(temporary, path)
            committed.append(path)
    except (OSError, ValueError) as exc:
        for path in reversed(committed):
            previous, metadata = originals[path]
            if previous is None:
                os.unlink(path)
            else:
                os.replace(stage(path, previous, metadata), path)
        raise ValueError(f'Cannot save Compose sources: {exc}') from exc
    finally:
        for temporary in staged.values():
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
