#!/usr/bin/env node
import { App, Tags } from "aws-cdk-lib";
import { AppStack } from "../lib/app-stack";
import { BaseStack } from "../lib/base-stack";

const app = new App();
const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT ?? "149320913851",
  region: process.env.CDK_DEFAULT_REGION ?? "eu-north-1",
};

const base = new BaseStack(app, "FaslaBase", { env, githubRepo: "uonuon/fasla", githubOwnerId: "15096591", githubRepoId: "1466697467" });
new AppStack(app, "FaslaApp", { env, appSecret: base.appSecret });

Tags.of(app).add("project", "fasla");
