import io
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.utils._errors import EntryNotFoundError
from loguru import logger

from .errors import AuthenticationError, NoSubmissionError, PastDeadlineError, SubmissionError, SubmissionLimitError
from .utils import http_get, http_post, user_authentication


@dataclass
class Submissions:
    competition_id: str
    submission_limit: str
    end_date: datetime
    autotrain_username: str
    autotrain_token: str
    autotrain_backend_api: str

    def __post_init__(self):
        self.public_sub_columns = [
            "date",
            "submission_id",
            "public_score",
            # "submission_comment",
            "selected",
            "status",
        ]
        self.private_sub_columns = [
            "date",
            "submission_id",
            "public_score",
            "private_score",
            # "submission_comment",
            "selected",
            "status",
        ]

    def _verify_submission(self, bytes_data):
        return True

    def _add_new_user(self, user_info):
        api = HfApi()
        user_submission_info = {}
        user_submission_info["name"] = user_info["name"]
        user_submission_info["id"] = user_info["id"]
        user_submission_info["submissions"] = []
        # convert user_submission_info to BufferedIOBase file object
        user_submission_info_json = json.dumps(user_submission_info)
        user_submission_info_json_bytes = user_submission_info_json.encode("utf-8")
        user_submission_info_json_buffer = io.BytesIO(user_submission_info_json_bytes)

        api.upload_file(
            path_or_fileobj=user_submission_info_json_buffer,
            path_in_repo=f"submission_info/{user_info['id']}.json",
            repo_id=self.competition_id,
            repo_type="dataset",
            token=self.autotrain_token,
        )

    def _check_user_submission_limit(self, user_info):
        user_id = user_info["id"]
        try:
            user_fname = hf_hub_download(
                repo_id=self.competition_id,
                filename=f"submission_info/{user_id}.json",
                use_auth_token=self.autotrain_token,
                repo_type="dataset",
            )
        except EntryNotFoundError:
            self._add_new_user(user_info)
            user_fname = hf_hub_download(
                repo_id=self.competition_id,
                filename=f"submission_info/{user_id}.json",
                use_auth_token=self.autotrain_token,
                repo_type="dataset",
            )
        except Exception as e:
            logger.error(e)
            raise Exception("Hugging Face Hub is unreachable, please try again later.")

        with open(user_fname, "r") as f:
            user_submission_info = json.load(f)

        todays_date = datetime.now().strftime("%Y-%m-%d")
        if len(user_submission_info["submissions"]) == 0:
            user_submission_info["submissions"] = []

        # count the number of times user has submitted today
        todays_submissions = 0
        for sub in user_submission_info["submissions"]:
            if sub["date"] == todays_date:
                todays_submissions += 1
        if todays_submissions >= self.submission_limit:
            return False
        return True

    def _increment_submissions(self, user_id, submission_id, submission_comment):
        user_fname = hf_hub_download(
            repo_id=self.competition_id,
            filename=f"submission_info/{user_id}.json",
            use_auth_token=self.autotrain_token,
            repo_type="dataset",
        )
        with open(user_fname, "r") as f:
            user_submission_info = json.load(f)
        todays_date = datetime.now().strftime("%Y-%m-%d")
        current_time = datetime.now().strftime("%H:%M:%S")

        # here goes all the default stuff for submission
        user_submission_info["submissions"].append(
            {
                "date": todays_date,
                "time": current_time,
                "submission_id": submission_id,
                "submission_comment": submission_comment,
                "status": "pending",
                "selected": False,
                "public_score": -1,
                "private_score": -1,
            }
        )
        # count the number of times user has submitted today
        todays_submissions = 0
        for sub in user_submission_info["submissions"]:
            if sub["date"] == todays_date:
                todays_submissions += 1

        # convert user_submission_info to BufferedIOBase file object
        user_submission_info_json = json.dumps(user_submission_info)
        user_submission_info_json_bytes = user_submission_info_json.encode("utf-8")
        user_submission_info_json_buffer = io.BytesIO(user_submission_info_json_bytes)
        api = HfApi()
        api.upload_file(
            path_or_fileobj=user_submission_info_json_buffer,
            path_in_repo=f"submission_info/{user_id}.json",
            repo_id=self.competition_id,
            repo_type="dataset",
            token=self.autotrain_token,
        )
        return todays_submissions

    def _download_user_subs(self, user_id):
        user_fname = hf_hub_download(
            repo_id=self.competition_id,
            filename=f"submission_info/{user_id}.json",
            use_auth_token=self.autotrain_token,
            repo_type="dataset",
        )
        with open(user_fname, "r") as f:
            user_submission_info = json.load(f)
        return user_submission_info["submissions"]

    def update_selected_submissions(self, user_token, selected_submission_ids):
        current_datetime = datetime.now()
        if current_datetime > self.end_date:
            raise PastDeadlineError("Competition has ended.")

        user_info = self._get_user_info(user_token)
        user_id = user_info["id"]

        user_fname = hf_hub_download(
            repo_id=self.competition_id,
            filename=f"submission_info/{user_id}.json",
            use_auth_token=self.autotrain_token,
            repo_type="dataset",
        )
        with open(user_fname, "r") as f:
            user_submission_info = json.load(f)

        for sub in user_submission_info["submissions"]:
            if sub["submission_id"] in selected_submission_ids:
                sub["selected"] = True
            else:
                sub["selected"] = False

        # convert user_submission_info to BufferedIOBase file object
        user_submission_info_json = json.dumps(user_submission_info)
        user_submission_info_json_bytes = user_submission_info_json.encode("utf-8")
        user_submission_info_json_buffer = io.BytesIO(user_submission_info_json_bytes)
        api = HfApi()
        api.upload_file(
            path_or_fileobj=user_submission_info_json_buffer,
            path_in_repo=f"submission_info/{user_id}.json",
            repo_id=self.competition_id,
            repo_type="dataset",
            token=self.autotrain_token,
        )

    def _get_user_subs(self, user_info, private=False):
        # get user submissions
        user_id = user_info["id"]
        try:
            user_submissions = self._download_user_subs(user_id)
        except EntryNotFoundError:
            raise NoSubmissionError("No submissions found ")

        submissions_df = pd.DataFrame(user_submissions)
        if not private:
            submissions_df = submissions_df.drop(columns=["private_score"])
            submissions_df = submissions_df[self.public_sub_columns]
        else:
            submissions_df = submissions_df[self.private_sub_columns]
        return submissions_df

    def _get_user_info(self, user_token):
        user_info = user_authentication(token=user_token)
        if "error" in user_info:
            raise AuthenticationError("Invalid token")

        if user_info["emailVerified"] is False:
            raise AuthenticationError("Please verify your email on Hugging Face Hub")
        return user_info

    def _create_autotrain_project(self, submission_id, competition_id, user_id, competition_type):

        # task: Literal["competition"]
        # competition_id = Field("", title="Competition ID")
        # competition_type = Field("", title="Competition Type")
        # user_id = Field("", title="Competition User ID")
        # submission_id = Field("", title="Submission ID")

        payload = {
            "username": self.autotrain_username,
            "proj_name": submission_id,
            "task": 26,
            "config": {
                "advanced": True,
                "language": "unk",
                "max_models": 1,
                "params": [
                    {
                        "task": "competition",
                        "competition_id": competition_id,
                        "competition_type": "generic",
                        "user_id": user_id,
                        "submission_id": submission_id,
                    }
                ],
            },
        }

        project_json_resp = http_post(
            path="/projects/create",
            payload=payload,
            token=self.autotrain_token,
            domain=self.autotrain_backend_api,
        ).json()

        time.sleep(5)

        # Process data
        _ = http_post(
            path=f"/projects/{project_json_resp['id']}/data/start_processing",
            token=self.autotrain_token,
            domain=self.autotrain_backend_api,
        ).json()

        logger.info("⏳ Waiting for data processing to complete ...")
        is_data_processing_success = False
        while is_data_processing_success is not True:
            project_status = http_get(
                path=f"/projects/{project_json_resp['id']}",
                token=self.autotrain_token,
                domain=self.autotrain_backend_api,
            ).json()
            # See database.database.enums.ProjectStatus for definitions of `status`
            if project_status["status"] == 3:
                is_data_processing_success = True
                logger.info("✅ Data processing complete!")
            time.sleep(3)

        # Approve training job
        _ = http_post(
            path=f"/projects/{project_json_resp['id']}/start_training",
            token=self.autotrain_token,
            domain=self.autotrain_backend_api,
        ).json()

    def my_submissions(self, user_token):
        user_info = self._get_user_info(user_token)
        current_date_time = datetime.now()
        private = False
        if current_date_time >= self.end_date:
            private = True
        subs = self._get_user_subs(user_info, private=private)
        return subs

    def new_submission(self, user_token, uploaded_file):
        # verify token
        user_info = self._get_user_info(user_token)

        # check if user can submit to the competition
        if self._check_user_submission_limit(user_info) is False:
            raise SubmissionLimitError("Submission limit reached")

        with open(uploaded_file.name, "rb") as f:
            bytes_data = f.read()
        # verify file is valid
        if not self._verify_submission(bytes_data):
            raise SubmissionError("Invalid submission file")
        else:
            user_id = user_info["id"]
            submission_id = str(uuid.uuid4())
            file_extension = uploaded_file.orig_name.split(".")[-1]
            # upload file to hf hub
            api = HfApi()
            api.upload_file(
                path_or_fileobj=bytes_data,
                path_in_repo=f"submissions/{user_id}-{submission_id}.{file_extension}",
                repo_id=self.competition_id,
                repo_type="dataset",
                token=self.autotrain_token,
            )
            # update submission limit
            submissions_made = self._increment_submissions(
                user_id=user_id,
                submission_id=submission_id,
                submission_comment="",
            )
            # schedule submission for evaluation
            self._create_autotrain_project(
                submission_id=f"{submission_id}",
                competition_id=f"{self.competition_id}",
                user_id=user_id,
                competition_type="generic",
            )
        remaining_submissions = self.submission_limit - submissions_made
        return remaining_submissions
