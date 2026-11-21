import * as path from "path";
import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from "aws-cdk-lib";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecsPatterns from "aws-cdk-lib/aws-ecs-patterns";
import * as logs from "aws-cdk-lib/aws-logs";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";

export interface AppStackProps extends StackProps {
  appSecret: secretsmanager.ISecret;
}

/**
 * The running app:  browser ── HTTPS ──> CloudFront ── HTTP ──> load balancer ──> Fargate task (Docker image of this repo)
 *
 * Cost notes: no NAT gateway (tasks sit in public subnets with a public IP and only accept traffic from the
 * load balancer), one task, 2-week log retention. CloudFront gives HTTPS on a *.cloudfront.net address
 * without buying a domain.
 */
export class AppStack extends Stack {
  constructor(scope: Construct, id: string, props: AppStackProps) {
    super(scope, id, props);

    const vpc = new ec2.Vpc(this, "Vpc", {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [{ name: "public", subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 }],
    });

    const cluster = new ecs.Cluster(this, "Cluster", { vpc, containerInsightsV2: ecs.ContainerInsights.DISABLED });

    const image = new ecrAssets.DockerImageAsset(this, "Image", {
      directory: path.join(__dirname, "..", ".."),
      platform: ecrAssets.Platform.LINUX_AMD64,
      exclude: ["infra", ".github", "tests", "data/*.db", "**/__pycache__", ".git"],
    });

    const logGroup = new logs.LogGroup(this, "Logs", {
      retention: logs.RetentionDays.TWO_WEEKS,
      removalPolicy: RemovalPolicy.RETAIN, // keep logs even if a deploy rolls back
    });

    const secret = (key: string) => ecs.Secret.fromSecretsManager(props.appSecret, key);

    const svc = new ecsPatterns.ApplicationLoadBalancedFargateService(this, "Web", {
      cluster,
      cpu: 1024, // planning is CPU-bound: ~1 vCPU for 30-90 s per plan
      memoryLimitMiB: 2048,
      desiredCount: 1,
      minHealthyPercent: 100, // keep the old task until the new one is healthy (no downtime)
      maxHealthyPercent: 200,
      assignPublicIp: true,
      taskSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      publicLoadBalancer: true,
      openListener: false, // only CloudFront may reach the load balancer (rule added below)
      circuitBreaker: { enable: true, rollback: true },
      healthCheckGracePeriod: Duration.seconds(90),
      taskImageOptions: {
        image: ecs.ContainerImage.fromDockerImageAsset(image),
        containerPort: 8000,
        logDriver: ecs.LogDrivers.awsLogs({ streamPrefix: "web", logGroup }),
        environment: {
          AUTH_MODE: "supabase",
          STORAGE_MODE: "supabase",
          SUPABASE_BUCKET: "fasla-files",
          PLAN_WORKERS: "1",
          SOLVER_SECONDS: "25",
          MAX_UPLOAD_MB: "10",
          GOOGLE_LOGIN: "0",
        },
        secrets: {
          SUPABASE_URL: secret("SUPABASE_URL"),
          SUPABASE_ANON_KEY: secret("SUPABASE_ANON_KEY"),
          SUPABASE_SERVICE_ROLE_KEY: secret("SUPABASE_SERVICE_ROLE_KEY"),
          SUPABASE_JWT_SECRET: secret("SUPABASE_JWT_SECRET"),
          DATABASE_URL: secret("DATABASE_URL"),
        },
      },
    });

    svc.targetGroup.configureHealthCheck({
      path: "/healthz",
      healthyHttpCodes: "200",
      interval: Duration.seconds(30),
      timeout: Duration.seconds(10),
    });
    svc.targetGroup.setAttribute("deregistration_delay.timeout_seconds", "30");

    // Let only CloudFront's origin-facing addresses reach the load balancer.
    const cloudfrontIps = ec2.PrefixList.fromLookup(this, "CloudFrontOriginFacing", {
      prefixListName: "com.amazonaws.global.cloudfront.origin-facing",
    });
    svc.loadBalancer.connections.allowFrom(
      ec2.Peer.prefixList(cloudfrontIps.prefixListId),
      ec2.Port.tcp(80),
      "CloudFront only",
    );

    const distribution = new cloudfront.Distribution(this, "Cdn", {
      comment: "Fasla",
      defaultBehavior: {
        origin: new origins.LoadBalancerV2Origin(svc.loadBalancer, {
          protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
          readTimeout: Duration.seconds(60),
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER,
      },
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
    });

    new CfnOutput(this, "AppUrl", { value: `https://${distribution.distributionDomainName}` });
    new CfnOutput(this, "LogGroupName", { value: logGroup.logGroupName });
  }
}
