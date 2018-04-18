#----------------------------------------------------------------------------
# Copyright 2018, FittedCloud, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#Unless required by applicable law or agreed to in writing, software
#distributed under the License is distributed on an "AS IS" BASIS,
#WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#See the License for the specific language governing permissions and
#limitations under the License.
#
#Author: Gregory Fedynyshyn (greg@fittedcloud.com)
#----------------------------------------------------------------------------

import sys
import os
import re
import collections
import time
import datetime
import traceback
import argparse
import csv
import json
import boto3
import botocore
import pprint # pretty printing!

from collections import namedtuple

FC_AWS_ENV = "AWS_DEFAULT_PROFILE"

# simple sanity check for start/end dates.  exceptions will occur with anything
# more complicatedly wrong
FC_MATCH_DATE = "[0-9]{4}-[0-1][0-9]-[0-3][0-9]"

# current valid values are MONTHLY and DAILY
FC_INTERVALS = ["MONTHLY", "DAILY"]

# a list of currently available dimensions by which to group
GROUP_DIMENSIONS = ["AZ",
                    "INSTANCE_TYPE",
                    "LINKED_ACCOUNT",
                    "OPERATION",
                    "PURCHASE_TYPE",
                    "REGION",
                    "SERVICE",
                    "USAGE_TYPE",
                    "USAGE_TYPE_GROUP",
                    "RECORD_TYPE",
                    "OPERATING_SYSTEM",
                    "TENANCY",
                    "SCOPE",
                    "PLATFORM",
                    "SUBSCRIPTION_ID",
                    "LEGAL_ENTITY_NAME",
                    "DEPLOYMENT_OPTION",
                    "DATABASE_ENGINE",
                    "CACHE_ENGINE",
                    "INSTANCE_TYPE_FAMILY"]

# We dynamically update regions in our software, but for the
# purposes of this script, hardcoding is fine.
AWS_REGIONS = [
    'us-east-1',       # US East (N. Virginia)
    'us-east-2',       # US East (Ohio)
    'us-west-1',       # US West (N. California)
    'us-west-2',       # US West (Oregon)
    'ca-central-1',    # Canada (Central)
    'eu-central-1',    # EU (Frankfurt)
    'eu-west-1',       # EU (Ireland)
    'eu-west-2',       # EU (London)
    'eu-west-3',       # EU (Paris)
    'ap-northeast-1',  # Asia Pacific (Tokyo)
    'ap-northeast-2',  # Asia Pacific (Seoul)
    'ap-northeast-3',  # Asia Pacific (Osaka-Local)
    'ap-southeast-1',  # Asia Pacific (Singapore)
    'ap-southeast-2',  # Asia Pacific (Sydney)
    'ap-south-1',      # Asia Pacific (Mumbai)
    'sa-east-1',       # South America (Sao Paulo)
]

# array of default abbreviations to use with output
ABBRV = {
    "AWS CloudTrail": "CT",
    "AWS Data Transfer": "DT",
    "AWS Key Managment Service": "KMS",
    "AWS Support (Developer)": "SD",
    "Amazon DynamoDB": "DDB",
    "Amazon Elastic Block Store": "EBS",
    "Amazon Elastic Compute Cloud - Compute": "EC2",
    "Amazon Relational Database Service": "RDS",
    "Amazon Simple Email Service": "SES",
    "Amazon Simple Notification Service": "SNS",
    "Amazon Simple Queue Service": "SQS",
    "Amazon Simple Storage Service": "S3",
    "AmazonCloudWatch": "CW",
    "Refund": "Ref" # everyone's favorite
} 

# simple abbreviation scheme:
#
# strip off beginning "Amazon" and "AWS" from service name
# then just use remaining uppercase letters to form abbreviation.
# currently not in use
def simple_abbreviation(string, suffix=""):
    abbr = ""
    if string.find("AWS") == 0:
        string = string[3:]
    elif string.find("Amazon") == 0:
        string = string[6:]
    for letter in string:
        if letter.isupper() or letter.isnumeric(): # numerals are okay too
            abbr += letter

    return abbr 

# currently not in use.  originally, would be used to generate abbreviations
# for better printing on the screen, but not really needed.
def build_abbreviations(a, s, r, start, end):
    abbrv = {}

    try:
        ce = boto3.client('ce',
                          aws_access_key_id=a,
                          aws_secret_access_key=s,
                          region_name=r) # not sure if region matters
        res = ce.get_dimension_values(SearchString="",
                                      TimePeriod={"Start":start, "End":end},
                                      Dimension="SERVICE",
                                      Context="COST_AND_USAGE")
        # TODO make sure there are no duplicates
        dims = res['DimensionValues']
        for k in dims:
            ab = simple_abbreviation(k['Value'])
            abbrv[k['Value']] = ab # FIXME duplicates will overwrite values here...
    except:
        e = sys.exc_info()
        print("ERROR: exception region=%s, error=%s" %(r, str(e)))
        traceback.print_exc()
    return abbrv

def get_costs(a, s, rlist, start, end, dims, tags, granularity="MONTHLY"):
    costs = []

    groupbys = []
    if len(dims) > 0 or len(tags) > 0:
        dims = dims.split(",")
        for d in dims:
            groupbys.append({"Type":"DIMENSION", "Key":d})

        tags = tags.split(",")
    if len(tags) > 0 and tags != [""]:
        for t in tags:
            groupbys.append({"Type":"TAG", "Key":t})

    if len(groupbys) == 0: # group by service by default
        groupbys.append({"Type":"DIMENSION", "Key":"SERVICE"})

    try:
        for r in rlist:
            ce = boto3.client('ce',
                              aws_access_key_id=a,
                              aws_secret_access_key=s,
                              region_name=r)


            if len(groupbys) > 0:
                res = ce.get_cost_and_usage(TimePeriod={"Start":start, "End":end},
                                            Granularity=granularity,
                                            Metrics=["BlendedCost", "UnblendedCost", "UsageQuantity"],
                                            GroupBy=groupbys)
            else:
                res = ce.get_cost_and_usage(TimePeriod={"Start":start, "End":end},
                                            Granularity=granularity,
                                            Metrics=["BlendedCost", "UnblendedCost", "UsageQuantity"])
            rbt = res['ResultsByTime']
            for groups in rbt:
                for group in groups['Groups']:
                # Metrics are of {'Amount':xxxxxx, 'Unit':xxxxxx}
                    cost = {
                        "region": r,
                        "estimated": groups['Estimated'],
                        "time_start": groups['TimePeriod']['Start'],
                        "time_end": groups['TimePeriod']['End'],
                        "group": group['Keys'],
                        #"srvabbr": ABBRV[group['Keys'][0]],
                        "blended_cost": group['Metrics']['BlendedCost'],
                        "unblended_cost": group['Metrics']['UnblendedCost'],
                        "usage_quantity": group['Metrics']['UsageQuantity']
                    }
                    costs.append(cost)
    except:
        e = sys.exc_info()
        print("ERROR: exception region=%s, error=%s" %(r, str(e)))
        traceback.print_exc()
    return costs

# we have some nested values in cost so we need to process the data
# before converting to CSV.  takes in dict, returns flattened dict
# dict cannot have lists, just sub-dicts
def flatten(d, parent_key='', sep='_'):
    items = []
    for k, v in d.items():
        new_key = parent_key + sep + k if parent_key else k
        if isinstance(v, collections.MutableMapping):
            items.extend(flatten(v, new_key, sep=sep).items())
        else:
            if type(v) == type(list()): # we don't have any lists with > 1 item
                v = v[0]
            items.append((new_key, v))
    return dict(items)

# output is [{'group': 'name', values:{'unblended_cost':xxx, 'unblended_unit':xxx,
#             'usage_quantity':xxx, 'usage_unit':xxx}}]
def consolidate_by_group(costs):
    out = []
    for cost in costs:
        found = 0

        # check if group already exists in list
        for i in range(0, len(out)):
            if out[i]['group'] == cost['group'][0]:
                found = 1
                out[i]['values']['unblended_cost'] += \
                        float(cost['unblended_cost']['Amount'])
                out[i]['values']['usage_quantity'] += \
                        float(cost['usage_quantity']['Amount'])
                break
        #else: # add to output
        if found == 0:
            tmp = {'group':cost['group'][0], 'values':{}}
            tmp['values'] = {'unblended_cost': float(cost['unblended_cost']['Amount']),
                             'unblended_unit': cost['unblended_cost']['Unit'],
                             'usage_quantity': float(cost['usage_quantity']['Amount']),
                             'usage_unit': cost['usage_quantity']['Unit']}
            out.append(tmp)
    return out

# pass return value from get_costs()
def print_results(costs, use_json=False, use_csv=False, start=None, end=None):
    if use_json == True:
        print(json.dumps(costs, sort_keys=True, indent=4))
    elif use_csv == True:
        flat_costs = []
        for cost in costs:
            flat_costs.append(flatten(cost))

        # print headers
        #    print(
        csv_writer = csv.DictWriter(sys.stdout, flat_costs[0].keys(), delimiter=",")
        csv_writer.writeheader()
        for cost in flat_costs:
            csv_writer.writerow(cost)
    else:
        out = consolidate_by_group(costs)
        print("\nSummary of costs: %s - %s\n" %(start, end))
        # print header.  hard-coded for now
        print("%s %61s" %("Group", "Cost"))
        print("%s %61s" %("-----", "----"))
        for cost in out:
            print("%-54s\t%14.2f %s"          \
                  %(cost['group'],
                  cost['values']['unblended_cost'],
                  cost['values']['unblended_unit']))

# human-readable option currently not used, so hide it from usage
def print_usage():
     print("costreporter.py <options>\n"
           "\tOptions are:\n\n"
           "\t--help - Display this help message\n"
           "\t-p --profile <profile name> - AWS profile name (can be used instead of -a and -s options)\n"
           "\t-a --accesskey <access key> - AWS access key\n"
           "\t-s --secretkey <secret key> - AWS secret key\n"
           "\t-r --regions <region1,region2,...> - A list of AWS regions.  If this option is omitted, all regions will be checked.\n"
           "\t-t --timerange - Time range as <start,end> time in format <YYYY-MM-DD>,<YYYY-MM-DD>\n"
           #"\t-h --human-readable <'k', 'm', or 'g'> display results in KB, MB, or GB.\n"
           "\t-j --json - Output in JSON format.\n"
           "\t-c --csv - Output as CSV.  Not compatible with --json.\n"
           "\t-d --dimension <dimension> - Group output by dimension (examples: AZ,INSTANCE_TYPE,LINKED_ACCOUNT,OPERATION,PURCHASE_TYPE,REGION,SERVICE,USAGE_TYPE,USAGE_TYPE_GROUP,RECORD_TYPE,OPERATING_SYSTEM,TENANCY,SCOPE,PLATFORM,SUBSCRIPTION_ID,LEGAL_ENTITY_NAME,DEPLOYMENT_OPTION,DATABASE_ENGINE,CACHE_ENGINE,INSTANCE_TYPE_FAMILY)\n"
           "\t-g --tag <tag name> - Group by tag name (list of names in format Tag1,Tag2,...,TagN).\n"
           "\t-i --interval <interval> - Dumps stats at <interval> granularity.  Valid values are MONTHLY (default) and DAILY."
           #"\t-b --abbrv - Output service abbreviations.\n\n"
           "\tOne of the following three parameters are required:\n"
           "\t\t1. Both the -a and -s options.\n"
           "\t\t2. The -p option.\n"
           "\t\t3. A valid " + FC_AWS_ENV + " enviornment variable.\n\n"
           "\tDepending on the number of EBS volumes being analyzed, this tool make take several minutes to run.")


def parse_options(argv):
    parser = argparse.ArgumentParser(prog="costreporter.py",
                     add_help=False) # use print_usage() instead

    parser.add_argument("-p", "--profile", type=str, required=False)
    parser.add_argument("-a", "--access-key", type=str, required=False)
    parser.add_argument("-s", "--secret-key", type=str, required=False)
    parser.add_argument("-r", "--regions", type=str, default="")
    parser.add_argument("-t", "--timerange", type=str, default="")
    #parser.add_argument("-h", "--human_readable", type=str, required=False, default='')
    parser.add_argument("-j", "--json", action="store_true", default=False)
    parser.add_argument("-c", "--csv", action="store_true", default=False)
    parser.add_argument("-d", "--dimension", type=str, default="")
    parser.add_argument("-g", "--tag", type=str, default="")
    parser.add_argument("-i", "--interval", type=str, default="MONTHLY")


    args = parser.parse_args(argv)
    if (len(args.regions) == 0):
        return args.profile, args.access_key, args.secret_key, [], args.timerange, args.json, args.csv, args.dimension, args.tag, args.interval
    else:
        return args.profile, args.access_key, args.secret_key, args.regions.split(','), args.timerange, args.json, args.csv, args.dimension, args.tag, args.interval


def parse_args(argv):
    # ArgumentParser's built-in way of automatically handling -h and --help
    # leaves much to be desired, so using this hack instead.
    for arg in argv:
        if (arg == '--help'):
            print_usage()
            os._exit(0)

    p, a, s, rList, t, j, c, d, g, i = parse_options(argv[1:])

    return p, a, s, rList, t, j, c, d, g, i


if __name__ == "__main__":
    p, a, s, rList, t, j, c, d, g, i = parse_args(sys.argv)

    # need either -a and -s, -p, or AWS_DEFAULT_PROFILE environment variable
    if not a and not s and not p:
        if (FC_AWS_ENV in os.environ):
            p = os.environ[FC_AWS_ENV]
        else:
            print_usage()
            print("\nError: must provide either -p option or -a and -s options")
            os._exit(1)

    if a and not s and not p:
        print_usage()
        print("\nError: must provide secret access key using -s option")
        os._exit(1)

    if not a and s and not p:
        print_usage()
        print("\nError: must provide access key using -a option")
        os._exit(1)

    if p:
        try:
            home = os.environ["HOME"]
            pFile = open(home + "/.aws/credentials", "r")
            line = pFile.readline()
            p = "["+p+"]"
            while p not in line:
                line = pFile.readline()
                if (line == ""): # end of file
                    print_usage()
                    print("\nError: invalid profile: %s" %p)
                    os._exit(1)

            # get access/secret keys
            a = pFile.readline().strip().split(" ")[2]
            s = pFile.readline().strip().split(" ")[2]

        except:
            print("Error: reading credentials for profile %s." %p)
            os._exit(1)

    if (len(rList) == 0):
        rList = AWS_REGIONS

    if j == True and c == True:
        print("Error: cannot specify both -j and -c")
        os._exit(1)

    time = t.split(",")

    # simple sanity check #1
    if len(time) != 2:
        print("Error: proper timerange format for <start,end> times is <YYYY-MM-DD>,<YYYY-MM-DD>")
        os._exit(1)
    start_time = time[0]
    end_time = time[1]

    # simple sanity check #2
    if re.match(FC_MATCH_DATE, start_time) == None or \
       re.match(FC_MATCH_DATE, end_time) == None:
        print("start_time = %s, match = %s" %(start_time, re.match(FC_MATCH_DATE, start_time)))
        print("end_time = %s, match = %s" %(end_time, re.match(FC_MATCH_DATE, end_time)))
        print("Error: proper timerange format for start, end times is <YYYY-MM-DD>,<YYYY-MM-DD>")
        os._exit(1)

    # simple sanity check for dimensions
    if d != "":
        dtmp = d.split(",")
        for dt in dtmp:
            if dt not in GROUP_DIMENSIONS:
                print("Error: invalid dimension: %s" %str(dt))
                os._exit(1)

    if i not in FC_INTERVALS:
        print("Error: invalid time interval: %s" %str(i))
        os._exit(1)

    # finally, let's get some cost data!
    try:
        # comment out customer service abbreviations for now
        #abbrv = build_abbreviations(a, s, rList[0], start_time, end_time)
        #ABBRV.update(abbrv)
        costs = get_costs(a, s, rList, start_time, end_time, d, g, i)
        print_results(costs, j, c, start_time, end_time)
    except:
        e = sys.exc_info()
        traceback.print_exc()
        os._exit(1)
