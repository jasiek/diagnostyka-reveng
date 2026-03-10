- We are going to reverse-engineer this app.
- Keep around a script (run.sh) which is going to contain reproducible steps to get to our goal.
  - Add to this script when you find out something is working for you.
- The objective is to discover the API this app uses under the hood. Ideally a swagger definition.
- Create a docker container within which you're going to install all tools which are necessary for reverse engineering.
- Mount the current directory in the docker container, so that the input and the output are in this directory.
- When you install a tool, add a note to README.md indicating what the tool is for.
- Commit after every change, keep changes in a commit logically related.
- Manage a fleet of subagents, give them instructions, to keep the context small.

