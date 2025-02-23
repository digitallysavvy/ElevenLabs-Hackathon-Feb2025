# Contributing Guidelines

Thank you for your interest in contributing to our Custom Conversational AI Agent project! This document provides guidelines and instructions for contributing.

## Code of Conduct

By participating in this project, you agree to abide by our Code of Conduct:

- Be respectful and inclusive
- Exercise consideration and empathy
- Focus on constructive criticism
- Avoid discriminatory or harassing behavior

## How to Contribute

### Reporting Issues

1. Check if the issue already exists in our issue tracker
2. If not, create a new issue with:
   - Clear title and description
   - Steps to reproduce (for bugs)
   - Expected vs actual behavior
   - Screenshots if applicable
   - System information when relevant

### Pull Requests

1. Fork the repository
2. Create a new branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. Make your changes following our coding standards
4. Commit your changes with clear messages:
   ```bash
   git commit -m "feat: add new feature" # for features
   git commit -m "fix: resolve issue #123" # for fixes
   ```
5. Push to your fork and submit a pull request

### Pull Request Guidelines

- Keep PRs focused on a single change
- Follow existing code style and conventions
- Include tests for new features
- Update documentation as needed
- Link related issues in PR description

## Development Setup

1. Install prerequisites:

   - Node.js (v14 or later)
   - Docker
   - Pulumi CLI
   - AWS CLI

2. Fork the repository: https://github.com/AgoraIO-Community/Custom-Conversational-AI-Agent-Deployer

   ```bash
   git clone https://github.com/{YOUR-USERNAME}/Custom-Conversational-AI-Agent-Deployer
   cd Custom-Conversational-AI-Agent-Deployer
   ```

3. Install dependencies:

   ```bash
   npm install
   ```

4. Set up your development environment following the README.md

## Coding Standards

- Use TypeScript for infrastructure code
- Follow ESLint and Prettier configurations
- Write meaningful comments and documentation
- Include type definitions
- Write unit tests for new features

## Testing

- Run tests before submitting PRs:
  ```bash
  npm test
  ```
- Ensure all existing tests pass
- Add new tests for new features
- Include both unit and integration tests

## Documentation

- Update README.md for significant changes
- Document new features and configuration options
- Include JSDoc comments for functions
- Update architecture diagrams if needed

## Release Process

1. Main branch is always deployable
2. Releases are tagged using semantic versioning
3. Release notes document all changes

## Getting Help

- Open an issue for questions
- Join our community chat
- Check existing documentation

## License

By contributing, you agree that your contributions will be licensed under the project's license.

Thank you for contributing to our project! 🎉
