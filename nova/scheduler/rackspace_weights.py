# Copyright (c) 2011 Rackspace Hosting
# All Rights Reserved.

"""
Rackspace soft rules
"""

from nova import flags
from nova.openstack.common import cfg

flag_opts = [
    cfg.FloatOpt('compute_empty_host_fn_weight',
            default=0.0,
            help='How much weight to give the empty_host cost function'),
    cfg.FloatOpt('compute_num_instances_fn_weight',
            default=5.0,
            help='How much weight to give the num_instances cost function'),
    cfg.FloatOpt('compute_num_instances_for_project_fn_weight',
            default=20.0,
            help='How much weight to give the project_id cost function'),
    cfg.FloatOpt('compute_num_instances_for_os_type_fn_weight',
            default=200000.0,
            help='How much weight to give the os_type cost function'),
    ]

FLAGS = flags.FLAGS
FLAGS.register_opts(flag_opts)


def compute_empty_host_fn(host_state, weight_properties=None):
    """At least 1 instance == lowest weight"""
    return 0 if host_state.num_instances else 1


def compute_num_instances_fn(host_state, weight_properties=None):
    """More instances == lower weight."""
    try:
        num_instances = host_state.num_instances
    except AttributeError:
        num_instances = 0
    return 200 - num_instances


def compute_num_instances_for_project_fn(host_state, weight_properties=None):
    """Less instances == lower weight."""
    try:
        project_id = weight_properties['project_id']
        num_instances = host_state.num_instances_by_project.get(project_id, 0)
    except (AttributeError, KeyError):
        num_instances = 0
    return num_instances + 200


def compute_num_instances_for_os_type_fn(host_state, weight_properties=None):
    """More instances == lower weight."""

    try:
        os_type_dict = host_state.num_instances_by_os_type
    except AttributeError:
        return 0

    try:
        os_type = weight_properties['os_type']
    except AttributeError:
        return 0

    other_type_num_instances = sum([os_type_dict[key]
            for key in os_type_dict.iterkeys()
                    if key != os_type])
    return other_type_num_instances


def test_it():
    from nova.scheduler import rackspace_host_manager
    import random

    weight_properties = {'os_type': 0,
                         'project_id': 0}

    scores = {}
    for x in xrange(1000):
        if x == 0:
            # make sure we have empty host in the list
            total_instances = 0
            for_project = 0
        else:
            total_instances = int(random.random() * 50)
            for_project = int(random.random() * total_instances)

        if int(random.random() * 2):
            for_os_type = 'windows'
        else:
            for_os_type = 'linux'
        if int(random.random() * 2):
            weight_properties['os_type'] = 'windows'
        else:
            weight_properties['os_type'] = 'linux'

        if weight_properties['os_type'] == for_os_type:
            same_type = True
            type_instances = 0
        else:
            same_type = False
            type_instances = int(random.random() * 3)
            if type_instances > total_instances:
                type_instances = total_instances

        hs = rackspace_host_manager.RackspaceHostState('host', 'topic')
        hs.num_instances = total_instances
        hs.num_instances_by_project = {0: for_project}
        if total_instances:
            hs.num_instances_by_os_type = {for_os_type: type_instances}
        else:
            hs.num_instances_by_os_type = {}
        empty = (compute_empty_host_fn(hs, weight_properties) *
                    FLAGS.compute_empty_host_fn_weight)
        num = (compute_num_instances_fn(hs, weight_properties) *
                    FLAGS.compute_num_instances_fn_weight)
        proj = (compute_num_instances_for_project_fn(hs,
                weight_properties) *
                        FLAGS.compute_num_instances_for_project_fn_weight)
        os_type = (compute_num_instances_for_os_type_fn(hs,
                weight_properties) *
                        FLAGS.compute_num_instances_for_os_type_fn_weight)
        score = empty + num + proj + os_type
        if score not in scores:
            scores[score] = set([])
        scores[score].add((total_instances, for_project,
            weight_properties['os_type'],
            same_type and 0 or type_instances))

    for score in sorted(scores.keys()):
        print "%5d: %s" % (score, scores[score])
