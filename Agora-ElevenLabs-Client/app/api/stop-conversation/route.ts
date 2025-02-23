import { NextResponse } from 'next/server';

const AGENT_ROUTER_URL = process.env.NEXT_PUBLIC_AGENT_ROUTER_URL;

export async function POST(request: Request) {
  try {
    if (!AGENT_ROUTER_URL) {
      throw new Error('AGENT_ROUTER_URL is not configured');
    }

    const clientID = request.headers.get('X-Client-ID');
    if (!clientID) {
      throw new Error('X-Client-ID header is required');
    }

    const body = await request.json();
    const { channel_name, uid } = body;

    const response = await fetch(`${AGENT_ROUTER_URL}/stop_agent`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Client-ID': clientID,
      },
      body: JSON.stringify({
        channel_name,
        uid: parseInt(uid, 10),
      }),
    });

    if (!response.ok) {
      throw new Error('Failed to stop conversation');
    }

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error('Error stopping conversation:', error);
    return NextResponse.json(
      { error: 'Failed to stop conversation' },
      { status: 500 }
    );
  }
}
