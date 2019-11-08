#! /bin/bash

STAGE=dev
SAM_BUCKET=sam-bucket-454679818906-us-west-2

sam build --use-container

sam package \
  --template-file .aws-sam/build/template.yaml \
  --region us-west-2 \
  --s3-bucket ${SAM_BUCKET} \
  --output-template-file packaged.yml

aws cloudformation deploy \
  --stack-name "offset-cloud-${STAGE}" \
  --template-file packaged.yml \
  --region us-west-2 \
  --no-fail-on-empty-changeset \
  --capabilities CAPABILITY_NAMED_IAM
