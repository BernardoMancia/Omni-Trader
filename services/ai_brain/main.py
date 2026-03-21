import asyncio
import logging
import numpy as np
import torch
import torch.nn as nn

logger = logging.getLogger("AIBrain")

class PPOActorCritic(nn.Module):
    def __init__(self, state_dim: int, action_dim: int):
        super().__init__()
        self.shared = nn.Sequential(
            nn.Linear(state_dim, 256), nn.ReLU(),
            nn.Linear(256, 128), nn.ReLU(),
        )
        self.actor = nn.Sequential(nn.Linear(128, action_dim), nn.Softmax(dim=-1))
        self.critic = nn.Linear(128, 1)

    def forward(self, x: torch.Tensor):
        features = self.shared(x)
        return self.actor(features), self.critic(features)

class PPOAgent:
    ACTIONS = ["HOLD", "BUY", "SELL"]

    def __init__(self, state_dim: int = 32, action_dim: int = 3):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = PPOActorCritic(state_dim, action_dim).to(self.device)
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=3e-4)

    def get_action(self, state_vector: np.ndarray, risk_state: str) -> dict:
        if risk_state == "RED":
            return {"action": "HOLD", "action_idx": 0, "confidence": 1.0}
        state_tensor = torch.FloatTensor(state_vector).unsqueeze(0).to(self.device)
        with torch.no_grad():
            probs, _ = self.model(state_tensor)
        action_idx = torch.argmax(probs).item()
        confidence = probs[0][action_idx].item()
        return {"action": self.ACTIONS[action_idx], "action_idx": action_idx, "confidence": round(confidence, 4)}

    def calculate_reward(self, pnl: float, slippage: float, fees: float) -> float:
        return pnl - (fees + 0.1 * np.sqrt(max(slippage, 0)))

    def update(self, states, actions, rewards, old_log_probs, clip_eps: float = 0.2):
        states_t = torch.FloatTensor(states).to(self.device)
        actions_t = torch.LongTensor(actions).to(self.device)
        rewards_t = torch.FloatTensor(rewards).to(self.device)
        old_log_probs_t = torch.FloatTensor(old_log_probs).to(self.device)

        probs, values = self.model(states_t)
        dist = torch.distributions.Categorical(probs)
        new_log_probs = dist.log_prob(actions_t)
        entropy = dist.entropy().mean()
        ratio = (new_log_probs - old_log_probs_t).exp()
        adv = (rewards_t - values.squeeze().detach())
        surr1 = ratio * adv
        surr2 = torch.clamp(ratio, 1 - clip_eps, 1 + clip_eps) * adv
        loss = -torch.min(surr1, surr2).mean() - 0.01 * entropy

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()
        return loss.item()

async def main():
    agent = PPOAgent()
    logger.info(f"AI Brain online | device={agent.device}")
    await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
