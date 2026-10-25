I want an app that can track security answers for online accounts.

The app needs to be able to generate unique, random answers to typical security questions such as, "The city I was born in" or "My mother's maiden name" and store those answers in a small, light database that can be persisted locally on my Windows computer.

When generating an answer, the app checks for similar or identical questions and ensures the answer it generates is unique. So if "Ithica, NY," Boise, ID" and "Sante Fe, NM" are already in the DB for the "which city were you born in" question, it would generate a new, unique answer.

The app should use AI to differentiate between the various questions, with the ability to take either a Open Router or Gemini API key to leverage those services. As far as answers go, I need your advice: Could the app use a package like Bogus to derive all the answers? 

As far as the database layout goes, we need a few pieces of information for each account:

- Account Name
- The security questions and answers, each in its own record
- Last date and time edited

The UI should allow the user to:

- Add, edit, and delete accounts (Add Account, Edit Account, Delete Account options)
- Manage an account's questions, adding a new question/answer (Add option), deleting a question/answer (Delete option) or editing an existing question/answer (once Edit Account is chosen, the fields are open for editing) and they can they save the updated account (Save option)
-Search for accounts and questions, using a universal search box and button that searches across all fields and populates results which can be viewed, edited, or deleted (Edit and Delete options)
- Export the entire DB as a CSV file that can be opened and viewed in Excel or Google Sheets so the accounts, questions and answeres can be viewed offline on other devices such as a phone or laptop (Export CSV option)
- Verify the integrity of the CSV file by comparing it to the DB (Verify CSV option)

I'm assuming Python as the language, SQL Lite as the DB but I'm open to recommendations, and I'm leaving the implementation to you. I'd prefer an app vs a web app simply because an app just starts, where as a web app requires you to run the server then visit a web site, but if you feel using a web app makes more sense, let's go with that. The biggest thing is I want a robust solution, I want real uniqueness, and I want a CSV that preserves the data in a readable form.

Are these requirements complete? Any questions?