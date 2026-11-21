import { CfnOutput, Duration, SecretValue, Stack, StackProps } from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";

export interface BaseStackProps extends StackProps {
  githubRepo: string; // "owner/repo"
  githubOwnerId?: string; // numeric GitHub IDs, as they appear in the OIDC token subject
  githubRepoId?: string;
}

/**
 * Things that are created once and rarely change:
 *  - the app's secret (values are filled in by hand in the console, never committed)
 *  - a GitHub OIDC trust + deploy role, so GitHub Actions deploys with short-lived credentials and no stored keys
 */
export class BaseStack extends Stack {
  readonly appSecret: secretsmanager.Secret;

  constructor(scope: Construct, id: string, props: BaseStackProps) {
    super(scope, id, props);
    const [owner, repo] = props.githubRepo.split("/");

    // Placeholders only. Every key must exist, because ECS refuses to start a task whose secret key is missing.
    // The real values are pasted in the Secrets Manager console.
    const empty = SecretValue.unsafePlainText("");
    this.appSecret = new secretsmanager.Secret(this, "AppSecret", {
      secretName: "fasla/app",
      description: "Fasla runtime settings: Supabase URL and keys, database URL",
      secretObjectValue: {
        SUPABASE_URL: empty,
        SUPABASE_ANON_KEY: empty,
        SUPABASE_SERVICE_ROLE_KEY: empty,
        SUPABASE_JWT_SECRET: empty,
        DATABASE_URL: empty,
      },
    });

    const provider = new iam.OpenIdConnectProvider(this, "GithubOidc", {
      url: "https://token.actions.githubusercontent.com",
      clientIds: ["sts.amazonaws.com"],
    });

    const deployRole = new iam.Role(this, "GithubDeployRole", {
      roleName: "fasla-github-deploy",
      description: "Assumed by GitHub Actions on the main branch of " + props.githubRepo,
      maxSessionDuration: Duration.hours(1),
      assumedBy: new iam.WebIdentityPrincipal(provider.openIdConnectProviderArn, {
        StringEquals: { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
        // GitHub's subject pins the owner and repo by numeric ID (repo:owner@ID/repo@ID:...), so a deleted and
        // re-created repo with the same name can't deploy. The name-only form is kept for older token formats.
        StringLike: {
          "token.actions.githubusercontent.com:sub": [
            `repo:${props.githubOwnerId ? `${owner}@${props.githubOwnerId}/${repo}@${props.githubRepoId}` : props.githubRepo}:ref:refs/heads/main`,
            `repo:${props.githubRepo}:ref:refs/heads/main`,
          ],
        },
      }),
    });
    // cdk deploy works through the roles that `cdk bootstrap` created; the deploy role only needs to assume them.
    deployRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["sts:AssumeRole"],
        resources: [`arn:aws:iam::${this.account}:role/cdk-*`],
      }),
    );

    new CfnOutput(this, "DeployRoleArn", { value: deployRole.roleArn });
    new CfnOutput(this, "AppSecretName", { value: this.appSecret.secretName });
  }
}
