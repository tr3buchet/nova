# Copyright (c) 2011 OpenStack, LLC.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""
Least Cost is an algorithm for choosing which host machines to
provision a set of resources to. The input is a WeightedHost object which
is decided upon by a set of objective-functions, called the 'cost-functions'.
The WeightedHost contains a combined weight for each cost-function.

The cost-function and weights are tabulated, and the host with the least cost
is then selected for provisioning.
"""

from nova import flags
from nova import log as logging
from nova.openstack.common import cfg


LOG = logging.getLogger(__name__)

least_cost_opts = [
    cfg.ListOpt('least_cost_functions',
                default=[
                  'nova.scheduler.least_cost.compute_fill_first_cost_fn'
                  ],
                help='Which cost functions the LeastCostScheduler should use'),
    cfg.FloatOpt('noop_cost_fn_weight',
             default=1.0,
               help='How much weight to give the noop cost function'),
    cfg.FloatOpt('compute_fill_first_cost_fn_weight',
             default=-1.0,
               help='How much weight to give the fill-first cost function. '
                    'A negative value will reverse behavior: '
                    'e.g. spread-first'),
    cfg.BoolOpt('rax_scheduler_num_hosts_to_fuzz',
                default=5,
                help='Number of hosts to include in scheduler weight '
                        'fuzzing.')
    ]

FLAGS = flags.FLAGS
FLAGS.register_opts(least_cost_opts)

# TODO(sirp): Once we have enough of these rules, we can break them out into a
# cost_functions.py file (perhaps in a least_cost_scheduler directory)


class WeightedHost(object):
    """Reduced set of information about a host that has been weighed.
    This is an attempt to remove some of the ad-hoc dict structures
    previously used."""

    def __init__(self, weight, host_state=None):
        self.weight = weight
        self.host_state = host_state

    def to_dict(self):
        x = dict(weight=self.weight)
        if self.host_state:
            x['host'] = self.host_state.host
        return x

    def __repr__(self):
        if self.host_state:
            return "WeightedHost [host: %s, weight: %s]" % (
                    self.host_state.host, self.weight)
        return "WeightedHost with no host_state"


def noop_cost_fn(host_state, weighing_properties):
    """Return a pre-weight cost of 1 for each host"""
    return 1


def compute_fill_first_cost_fn(host_state, weighing_properties):
    """More free ram = higher weight. So servers will less free
    ram will be preferred."""
    return host_state.free_ram_mb


def _get_weighted_hosts(weighted_fns, host_states, weighing_properties):
    min_score, best_host = None, None
    weighted_hosts = []
    for host_state in host_states:
        score = sum(weight * fn(host_state, weighing_properties)
                    for weight, fn in weighted_fns)
        if min_score is None or score < min_score:
            min_score, best_host = score, host_state
        weighted_hosts.append(WeightedHost(score, host_state=host_state))
    return (weighted_hosts, min_score, best_host)


def weighted_sum(weighted_fns, host_states, weighing_properties):
    """Use the weighted-sum method to compute a score for an array of objects.

    Normalize the results of the objective-functions so that the weights are
    meaningful regardless of objective-function's range.

    :param host_list:    ``[(host, HostInfo()), ...]``
    :param weighted_fns: list of weights and functions like::

        [(weight, objective-functions), ...]

    :param weighing_properties: an arbitrary dict of values that can
        influence weights.

    :returns: a single WeightedHost object which represents the best
              candidate.
    """

    if not host_states:
        return

    weighted_hosts, min_score, best_host = _get_weighted_hosts(weighted_fns,
            host_states, weighing_properties)

    if FLAGS.rax_scheduler_num_hosts_to_fuzz:
        import random
        sorted_hosts = sorted(weighted_hosts, key=lambda x: x.weight)
        num_hosts = min(len(sorted_hosts),
                FLAGS.rax_scheduler_num_hosts_to_fuzz)
        return sorted_hosts[int(random.random() * num_hosts)]

    return WeightedHost(min_score, host_state=best_host)
