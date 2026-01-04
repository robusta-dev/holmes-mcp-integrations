const { Server } = require('@modelcontextprotocol/sdk/server/index.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const { AwsAccountAuth } = require('./lib/aws-account-auth.js');

// Import AWS SDK clients as needed
const { EC2Client, DescribeInstancesCommand } = require('@aws-sdk/client-ec2');
const { S3Client, ListBucketsCommand } = require('@aws-sdk/client-s3');
// ... other AWS clients

const auth = new AwsAccountAuth();

const server = new Server(
  {
    name: 'multi-account-aws-mcp',
    version: '1.0.0',
  },
  {
    capabilities: {
      tools: {},
    },
  }
);

// Initialize auth on startup
auth.initialize().catch(console.error);

// Register tools that use multiple profiles
server.setRequestHandler('tools/list', async () => {
  return {
    tools: [
      {
        name: 'aws_describe_instances',
        description: 'Describe EC2 instances in a specific account',
        inputSchema: {
          type: 'object',
          properties: {
            profile: {
              type: 'string',
              description: 'Account profile (dev, prod, etc.)',
              enum: auth.getProfiles(),
            },
            region: {
              type: 'string',
              description: 'AWS region',
              default: 'us-east-2',
            },
          },
          required: ['profile'],
        },
      },
      // Add more tools here
    ],
  };
});

server.setRequestHandler('tools/call', async (request) => {
  const { name, arguments: args } = request.params;

  if (name === 'aws_describe_instances') {
    try {
      const ec2Client = await auth.client(args.profile, EC2Client, {
        region: args.region,
      });
      
      const command = new DescribeInstancesCommand({});
      const response = await ec2Client.send(command);
      
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(response, null, 2),
          },
        ],
      };
    } catch (error) {
      throw new Error(`Failed to describe instances: ${error.message}`);
    }
  }

  throw new Error(`Unknown tool: ${name}`);
});

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error('Multi-account AWS MCP server running on stdio');
}

main().catch((error) => {
  console.error('Server error:', error);
  process.exit(1);
});
