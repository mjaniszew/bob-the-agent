# Implement AWS S3 Support

Research and implement handlig AWS S3 buckets via AWS SKD.

## Definition of Done
1. Implementation plan is created and saved in `docs/plans`. Plan follows these rules:
 - it describes s3 integration via AWS SDK. Integration supports file upload to s3, as well as generating authorized urls for files in s3 bucket
 - it describes implementing new `aws-s3` skill which will use s3 interation in simillar way as other skills are implemented
 - it describes a way to test whether file is properly uploaded to s3 bucket and accessible
 - it forces execution in order: 1. Tests are created 2. Tests are failing 3. Logic is implemented 4. Tests are passing
 - plan and tests contain example text file with lorem ipsu to be uploaded to s3 bucket
2. Implementation plan is executed in proper order
3. `aws-s3` skill when ivoked is able to: 
 - properly upload file to s3 bucket
 - generate authorized url for uploaded file
4. `aws-s3` skill along any archidecture doc files in `docs` and agent files in `src/agents` are updated and reflect new implementation. Only these agents should be able to use that skill: `main`, `data-extractor`, `document-creator`, `web-searcher`
5. `src/skills/index.ts` is up to date with new implementation

## Development rules and context
1. Use subagents to execute every step. Agents need to save their ouptut results in `/app/data` and report output files back to main agent
2. Use official aws sdk to implement s3 upload and url generation
3. For logic, use backet name and S3 auth key coming from env variables. You will have them available in `.env` under these keys:
 - AWS_S3_BUCKET - S3 Bucket name
 - AWS_S3_REGION - S3 Region where bucket belongs to
 - AWS_ACCESS_KEY_ID - S3 Access key
 - AWS_SECRET_ACCESS_KEY - S3 Access key secret
3. Validate whether logic works with e2e tests: use aws sdk to upload and access uploaded files. If logic fails, fix and repeat unit all issues will be resolved.
4. If you need add new npm dependencies, add them in correct place eg. in `src/skills/package.json` and test there, not from root of the project.
5. Search internet only with up to date documentations from this year.
6. Never harcode any values in code, use feasible variables, especially fom `.env`. Use env variables where applicable.
7. Test in docker, never outside container, with .env file loaded

## Additional resources and help to fetch and use
- Agent Overview: `docs/OVERVIEW.md` - contains general information about agent, its purpose, and main assumptions. Read this first
- Agent Skills: `docs/features/TOOLS.md` - contains list of particular tools agent should have implemented along with purpose descriptions and requirements particular tool should fulfill in order to be considered as working. Focus on `Agent Browser` tool
- Skills are defined in `src/skills/`