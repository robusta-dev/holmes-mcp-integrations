#!/usr/bin/env node
// Simple test MCP server to verify supergateway base image works

const readline = require('readline');

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  terminal: false
});

function sendResponse(id, result) {
  const response = JSON.stringify({ jsonrpc: '2.0', id, result });
  process.stdout.write(response + '\n');
}

function sendError(id, code, message) {
  const response = JSON.stringify({ jsonrpc: '2.0', id, error: { code, message } });
  process.stdout.write(response + '\n');
}

rl.on('line', (line) => {
  try {
    const request = JSON.parse(line);
    const { id, method, params } = request;

    switch (method) {
      case 'initialize':
        sendResponse(id, {
          protocolVersion: '2024-11-05',
          capabilities: { tools: {} },
          serverInfo: { name: 'test-mcp-server', version: '1.0.0' }
        });
        break;

      case 'tools/list':
        sendResponse(id, {
          tools: [{
            name: 'echo',
            description: 'Echo back the input message',
            inputSchema: {
              type: 'object',
              properties: {
                message: { type: 'string', description: 'Message to echo' }
              },
              required: ['message']
            }
          }]
        });
        break;

      case 'tools/call':
        if (params?.name === 'echo') {
          sendResponse(id, {
            content: [{ type: 'text', text: `Echo: ${params.arguments?.message || ''}` }]
          });
        } else {
          sendError(id, -32601, `Unknown tool: ${params?.name}`);
        }
        break;

      case 'notifications/initialized':
        // No response needed for notifications
        break;

      default:
        sendError(id, -32601, `Method not found: ${method}`);
    }
  } catch (err) {
    sendError(null, -32700, `Parse error: ${err.message}`);
  }
});

process.stderr.write('Test MCP server started\n');
