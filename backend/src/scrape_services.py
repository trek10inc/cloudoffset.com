#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright 2018 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this
# software and associated documentation files (the "Software"), to deal in the Software
# without restriction, including without limitation the rights to use, copy, modify,
# merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
# PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


from __future__ import print_function

import os
import sys

# Required to load modules from vendored subfolder (for clean development env)
sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), "./vendored"))

import re
import json
from collections import defaultdict
import boto3
import datetime
import logging
import pandas as pd

# For date
from dateutil.relativedelta import relativedelta

session = boto3.Session()

# For email
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import COMMASPACE, formatdate

# GLOBALS
SES_REGION = os.environ.get("SES_REGION")
if not SES_REGION:
    SES_REGION = "us-east-1"
ACCOUNT_LABEL = os.environ.get("ACCOUNT_LABEL")
if not ACCOUNT_LABEL:
    ACCOUNT_LABEL = "Email"

CURRENT_MONTH = os.environ.get("CURRENT_MONTH")
if CURRENT_MONTH == "true":
    CURRENT_MONTH = True
else:
    CURRENT_MONTH = False

LAST_MONTH_ONLY = os.environ.get("LAST_MONTH_ONLY")

# Default exclude support, as for Enterprise Support
# as support billing is finalised later in month so skews trends
INC_SUPPORT = os.environ.get("INC_SUPPORT")
if INC_SUPPORT == "true":
    INC_SUPPORT = True
else:
    INC_SUPPORT = False

TAG_VALUE_FILTER = os.environ.get("TAG_VALUE_FILTER") or "*"
TAG_KEY = os.environ.get("TAG_KEY")


class CostExplorer:
    """Retrieves BillingInfo checks from CostExplorer API
    >>> costexplorer = CostExplorer()
    >>> costexplorer.addReport(GroupBy=[{"Type": "DIMENSION","Key": "SERVICE"}])
    >>> costexplorer.generateExcel()
    """

    def __init__(self, CurrentMonth=False):
        # Array of reports ready to be output to Excel.
        self.reports = []
        self.client = session.client("ce", region_name="us-east-1")
        self.end = datetime.date.today().replace(day=1)
        self.riend = datetime.date.today()
        if CurrentMonth or CURRENT_MONTH:
            self.end = self.riend

        if LAST_MONTH_ONLY:
            self.start = (datetime.date.today() - relativedelta(months=+1)).replace(
                day=1
            )  # 1st day of month a month ago
        else:
            # Default is last 12 months
            self.start = (datetime.date.today() - relativedelta(months=+12)).replace(
                day=1
            )  # 1st day of month 12 months ago

        self.ristart = (datetime.date.today() - relativedelta(months=+11)).replace(
            day=1
        )  # 1st day of month 11 months ago
        self.sixmonth = (datetime.date.today() - relativedelta(months=+6)).replace(
            day=1
        )  # 1st day of month 6 months ago, so RI util has savings values
        try:
            self.accounts = self.getAccounts()
        except:
            logging.exception("Getting Account names failed")
            self.accounts = {}

    def getAccounts(self):
        accounts = {}
        client = session.client("organizations", region_name="us-east-1")
        paginator = client.get_paginator("list_accounts")
        for response in paginator.paginate():
            for acc in response["Accounts"]:
                accounts[acc["Id"]] = acc
        return accounts


    def addReport(
        self,
        Name="Default",
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        Style="Total",
        Region=None,
        NoCredits=True,
        CreditsOnly=False,
        RefundOnly=False,
        UpfrontOnly=False,
        IncSupport=False,
    ):
        type = "chart"  # other option table
        results = []
        if not NoCredits:
            response = self.client.get_cost_and_usage(
                TimePeriod={
                    "Start": self.start.isoformat(),
                    "End": self.end.isoformat(),
                },
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
                GroupBy=GroupBy,
            )
        else:
            Filter = {"And": []}

            Dimensions = {
                "Not": {
                    "Dimensions": {
                        "Key": "RECORD_TYPE",
                        "Values": ["Credit", "Refund", "Upfront", "Support"],
                    }
                }
            }
            if (
                INC_SUPPORT or IncSupport
            ):  # If global set for including support, we dont exclude it
                Dimensions = {
                    "Not": {
                        "Dimensions": {
                            "Key": "RECORD_TYPE",
                            "Values": ["Credit", "Refund", "Upfront"],
                        }
                    }
                }
            if CreditsOnly:
                Dimensions = {
                    "Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit"]}
                }
            if RefundOnly:
                Dimensions = {
                    "Dimensions": {"Key": "RECORD_TYPE", "Values": ["Refund"]}
                }
            if UpfrontOnly:
                Dimensions = {
                    "Dimensions": {"Key": "RECORD_TYPE", "Values": ["Upfront"]}
                }

            tagValues = None
            if TAG_KEY:
                tagValues = self.client.get_tags(
                    SearchString=TAG_VALUE_FILTER,
                    TimePeriod={
                        "Start": self.start.isoformat(),
                        "End": datetime.date.today().isoformat(),
                    },
                    TagKey=TAG_KEY,
                )

            if tagValues:
                Filter["And"].append(Dimensions)
                if len(tagValues["Tags"]) > 0:
                    Tags = {"Tags": {"Key": TAG_KEY, "Values": tagValues["Tags"]}}
                    Filter["And"].append(Tags)
            else:
                Filter = Dimensions.copy()
            if Region:
                Filter= {'And': [{'Dimensions': {'Key': 'REGION', 'Values': [Region]}}, Filter]}

            response = self.client.get_cost_and_usage(
                TimePeriod={
                    "Start": self.start.isoformat(),
                    "End": self.end.isoformat(),
                },
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
                GroupBy=GroupBy,
                Filter=Filter,
            )

        if response:
            results.extend(response["ResultsByTime"])

            while "nextToken" in response:
                nextToken = response["nextToken"]
                response = self.client.get_cost_and_usage(
                    TimePeriod={
                        "Start": self.start.isoformat(),
                        "End": self.end.isoformat(),
                    },
                    Granularity="MONTHLY",
                    Metrics=["UnblendedCost"],
                    GroupBy=GroupBy,
                    NextPageToken=nextToken,
                )

                results.extend(response["ResultsByTime"])
                if "nextToken" in response:
                    nextToken = response["nextToken"]
                else:
                    nextToken = False
        rows = []
        sort = ""
        for v in results:
            row = {"date": v["TimePeriod"]["Start"]}
            sort = v["TimePeriod"]["Start"]
            for i in v["Groups"]:
                key = i["Keys"][0]
                if key in self.accounts:
                    key = self.accounts[key][ACCOUNT_LABEL]
                row.update({key: float(i["Metrics"]["UnblendedCost"]["Amount"])})
            if not v["Groups"]:
                row.update({"Total": float(v["Total"]["UnblendedCost"]["Amount"])})
            rows.append(row)

        df = pd.DataFrame(rows)
        df.set_index("date", inplace=True)
        df = df.fillna(0.0)

        if Style == "Change":
            dfc = df.copy()
            lastindex = None
            for index, row in df.iterrows():
                if lastindex:
                    for i in row.index:
                        try:
                            df.at[index, i] = dfc.at[index, i] - dfc.at[lastindex, i]
                        except:
                            logging.exception("Error")
                            df.at[index, i] = 0
                lastindex = index
        df = df.T
        df = df.sort_values(sort, ascending=False)
        self.reports.append({"Name": Name, "Data": df, "Type": type})
        return df

    def generateExcel(self):
        # Create a Pandas Excel writer using XlsxWriter as the engine.\
        os.chdir("/tmp")
        writer = pd.ExcelWriter("cost_explorer_report.xlsx", engine="xlsxwriter")
        workbook = writer.book
        for report in self.reports:
            print(report["Name"], report["Type"])
            report["Data"].to_excel(writer, sheet_name=report["Name"])
            worksheet = writer.sheets[report["Name"]]
        writer.save()

        # Time to deliver the file to S3
        if os.environ.get("S3_BUCKET"):
            s3 = session.client("s3")
            s3.upload_file(
                "cost_explorer_report.xlsx",
                os.environ.get("S3_BUCKET"),
                "cost_explorer_report.xlsx",
            )
        if os.environ.get("SES_SEND"):
            # Email logic
            msg = MIMEMultipart()
            msg["From"] = os.environ.get("SES_FROM")
            msg["To"] = COMMASPACE.join(os.environ.get("SES_SEND").split(","))
            msg["Date"] = formatdate(localtime=True)
            msg["Subject"] = "Cost Explorer Report"
            text = "Find your Cost Explorer report attached\n\n"
            msg.attach(MIMEText(text))
            with open("cost_explorer_report.xlsx", "rb") as fil:
                part = MIMEApplication(fil.read(), Name="cost_explorer_report.xlsx")
            part["Content-Disposition"] = (
                'attachment; filename="%s"' % "cost_explorer_report.xlsx"
            )
            msg.attach(part)
            # SES Sending
            ses = session.client("ses", region_name=SES_REGION)
            result = ses.send_raw_email(
                Source=msg["From"],
                Destinations=os.environ.get("SES_SEND").split(","),
                RawMessage={"Data": msg.as_string()},
            )


def main_handler(event=None, context=None):
    costexplorer = CostExplorer(CurrentMonth=False)
    # Default addReport has filter to remove Support / Credits / Refunds / UpfrontRI
    # Overall Billing Reports
    # costexplorer.addReport(Name="Total", GroupBy=[],Style='Total',IncSupport=True)
    # costexplorer.addReport(Name="TotalChange", GroupBy=[],Style='Change')
    # costexplorer.addReport(Name="TotalInclCredits", GroupBy=[],Style='Total',NoCredits=False,IncSupport=True)
    # costexplorer.addReport(Name="TotalInclCreditsChange", GroupBy=[],Style='Change',NoCredits=False)
    # costexplorer.addReport(Name="Credits", GroupBy=[],Style='Total',CreditsOnly=True)
    # costexplorer.addReport(Name="Refunds", GroupBy=[],Style='Total',RefundOnly=True)
    # costexplorer.addReport(Name="RIUpfront", GroupBy=[],Style='Total',UpfrontOnly=True)
    # GroupBy Reports
    usage_type = costexplorer.addReport(
        Name="Usage",
        GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
        Style="Total",
    )
    costexplorer.addReport(
        Name="Instances",
        GroupBy=[{"Type": "DIMENSION", "Key": "INSTANCE_TYPE"}],
        Style="Total",
    )
    costexplorer.addReport(
        Name="Services",
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        Style="Total",
        IncSupport=True,
    )
    # costexplorer.addReport(Name="ServicesChange", GroupBy=[{"Type": "DIMENSION","Key": "SERVICE"}],Style='Change')
    #costexplorer.addReport(
    #    Name="Accounts",
    #    GroupBy=[{"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"}],
    #    Style="Total",
    #)
    # costexplorer.addReport(Name="AccountsChange", GroupBy=[{"Type": "DIMENSION","Key": "LINKED_ACCOUNT"}],Style='Change')
    regions = costexplorer.addReport(
        Name="Regions", GroupBy=[{"Type": "DIMENSION", "Key": "REGION"}], Style="Total"
    )
    # costexplorer.addReport(Name="RegionsChange", GroupBy=[{"Type": "DIMENSION","Key": "REGION"}],Style='Change')

    all_used_regions = list(next(regions.items())[1].to_dict().keys())
    print(f"Using regions {all_used_regions}")
    regional_reports = {}
    #for region in all_used_regions:
    for region in ['us-east-2']:
        if region in ['NoRegion', 'global']:
            continue
        regional_reports[region] = costexplorer.addReport(
            Name=region,
            GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
            Region=region,
            Style="Total",
        )
        calculate_carbon(regional_reports[region])


    costexplorer.generateExcel()


    #from ptpython.repl import embed
    #embed(globals(), locals(), vi_mode=True)

    #for month, report in usage_type.items():
    #    categorize_month(report.to_dict())

    return "Report Generated"

def lambda_handler(event, context):
    global session
    session = boto3.Session()
    print(json.dumps(event, sort_keys=True))
    main_handler()
    s3 = boto3.resource('s3')
    bucket = s3.Bucket(os.getenv("REPORT_STORAGE"))

    bucket.upload_file(
        '/tmp/cost_explorer_report.xlsx',
        boto3.session('sts').get_caller_identity()['Account'] + '.xlsx'
    )

def calculate_carbon(regional_report):
    for month, usage in regional_report.items():
        for usage_type, amount in usage.to_dict().items():
            if amount < 1:
                continue
            if usage_type.endswith('Bytes') and amount < 10**9:
                continue
            print(month, usage_type, amount)
    from ptpython.repl import embed
    embed(globals(), locals(), vi_mode=True)

def divine_region(usage_type_name):
    mapping = dict(
        USW1='us-west-1',
        USW2='us-west-2',
        USE1='us-east-1',
        USE2='us-east-2',
        EUN1="eu-north-1",
        EU="eu-north-1",
        APS3="ap-south-1",
        EUW3="eu-west-3",
        EUW2="eu-west-2",
        EUW1="eu-west-1",
        APN2="ap-northeast-2",
        APN1="ap-northeast-1",
        SAE1="sa-east-1",
        CAN1="ca-central-1",
        APS1="ap-southeast-1",
        APS2="ap-southeast-2",
        EUC1="eu-central-1",
    )

    for k, v in mapping.items():
        if usage_type_name.startswith(k):
            return v
        if usage_type_name.startswith(v):
            return v
    if re.match('^(Box|Instance|Node|Heavy)Usage', usage_type_name):
        return 'us-east-1'
    if re.match('^(RDS:|)(Multi-AZ|)(Usage:db|-?GP2|-?PIOPS|StorageUsag)', usage_type_name):
        return 'us-east-1'
    if re.match('^EBS(Optimized|):', usage_type_name):
        return 'us-east-1'
    if re.match('^ets-(hd|sd|audio)-', usage_type_name):
        return 'us-east-1'
    if usage_type_name.startswith('agent-assessment'):
        return 'us-east-1'
    if usage_type_name.startswith('Storage-ShardHour'):
        return 'us-east-1'
    if usage_type_name.startswith('Lambda-GB-Second'):
        return 'us-east-1'
    print(f"WARN: Could not regionalize {usage_type_name}")
    return None

def categorize_month(month):
    regional = defaultdict(list)
    for k, v in month.items():
        if v < 1:
            continue
        region = divine_region(k)
    pass


if __name__ == "__main__":
    main_handler()
