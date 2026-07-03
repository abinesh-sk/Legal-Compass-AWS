# Legal Compass — AI-Powered Legal Aid Chatbot

> Legal Guidance Made Simple — Free, Anonymous, Multilingual

## Live Demo



## Architecture
![Legal Compass Architecture](Demo%20Images/architecture1.png)

## Tech Stack
- **Frontend:** React.js, AWS Amplify, CloudFront, S3
- **Auth:** Amazon Cognito (anonymous identity pool)
- **API:** Amazon API Gateway (REST, AWS_IAM auth)
- **Compute:** AWS Lambda (Python 3.12)
- **AI/ML:** Amazon Bedrock (Nova Lite), Amazon Lex V2
- **Translation:** Amazon Translate (75+ languages)
- **Storage:** Amazon DynamoDB, Amazon S3
- **Monitoring:** Amazon CloudWatch

## Features
- Answers legal questions in 75+ languages
- RAG pipeline grounded in verified legal documents
- No signup required — fully anonymous
- Conversation memory across messages
- Intent classification across 4 legal domains
- Source citations for every answer

## AWS Services Used (13 total)
Lambda · DynamoDB · S3 · API Gateway · Cognito ·
Bedrock · Lex V2 · Translate · CloudFront · 
CloudWatch · IAM · CloudTrail · EventBridge

## Cost
~$0.0001 per conversation (essentially free at scale)
