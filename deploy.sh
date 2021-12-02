#!/bin/bash

# デプロイ先のプロジェクトをセットします
gcloud config set project <your-project-id>

# Cloud Functionをデプロイ
gcloud functions deploy freee-main\
		--source main\
		--env-vars-file env.yaml\
		--entry-point mainpage\
		--memory 128mb \
		--runtime python38 --region asia-northeast1 --trigger-http --allow-unauthenticated 