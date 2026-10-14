"""Separate strategy labels from the verified control safety contract.

Known V2.1 configurations migrate to contract 2. Unknown versions fail closed;
only the original configuration without a version remains a legacy comparison.
"""


def normalize_verified_contract(config):
    upgrade = config.get('upgrade_version')
    strategy = config.get('strategy_version')
    if upgrade not in (None, '2.1', '2.2') or strategy not in (None, '2.1', '2.2'):
        raise ValueError('Unsupported strategy version')
    if upgrade is not None and strategy is not None and upgrade != strategy:
        raise ValueError('Conflicting upgrade_version and strategy_version')
    if 'control_contract_version' in config:
        contract = config['control_contract_version']
        if type(contract) is not int or contract != 2:
            raise ValueError('Unsupported verified-control contract (requires integer 2)')
    elif upgrade == '2.1' and strategy != '2.2':
        config['control_contract_version'] = 2
    elif upgrade == '2.2' or strategy is not None:
        raise ValueError('Versioned strategy requires explicit control_contract_version=2')
    return config


def uses_verified_control(config):
    return normalize_verified_contract(dict(config)).get('control_contract_version') == 2


def uses_range_gate(config):
    return config.get('strategy_version', config.get('upgrade_version')) == '2.2'
